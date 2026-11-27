"""Validation of what admins store: capability config per kind, operation limits and argument schemas,
connection config and secrets. Everything raises ValueError with a message the API can show."""
import ipaddress
import re
from typing import Literal
from urllib.parse import urlsplit

import jsonschema
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from capabilities.catalog import SECRET_FIELDS, reject_secrets

PLAIN_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
MAX_SCHEMA_CHARS = 20_000
MAX_CONFIG_CHARS = 8_000


def _plain_filename(value: str, what: str) -> str:
    if not PLAIN_FILENAME.match(value or "") or value in (".", ".."):
        raise ValueError(f"{what} debe ser un nombre de archivo, sin rutas ni espacios")
    return value


def _local_host(host: str) -> bool:
    """localhost, a loopback/private address, or a single-label name (a container on the same network)."""
    if host in ("localhost",) or "." not in host and ":" not in host:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or address.is_private


class MCPConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transport: Literal["streamable_http", "stdio"] = "streamable_http"
    url: str | None = Field(None, max_length=500)
    command: str | None = None  # stdio: executable name, looked up in CAPABILITY_BIN_DIR (WP5/WP6)
    args: list[str] = Field(default_factory=list, max_length=20)
    timeout_seconds: int = Field(30, ge=1, le=300)
    # Calls per minute through this server (all users together, per app process)
    rate_per_minute: int = Field(60, ge=1, le=1000)
    # Name of a read-only guard in capabilities/guards.py for this server's arguments (WP7/WP8)
    guard: str | None = Field(None, pattern=r"^[a-z][a-z0-9_]{1,40}$")

    @field_validator("args")
    @classmethod
    def short_args(cls, value: list[str]) -> list[str]:
        if any(len(a) > 200 for a in value):
            raise ValueError("cada argumento puede tener como máximo 200 caracteres")
        return value

    @model_validator(mode="after")
    def endpoint(self):
        if self.transport == "streamable_http":
            if not self.url:
                raise ValueError("url es obligatoria para streamable_http")
            parts = urlsplit(self.url)
            if parts.scheme not in ("https", "http") or not parts.hostname:
                raise ValueError("url debe ser una URL http(s) completa")
            if parts.username or parts.password:
                raise ValueError("url no puede llevar usuario ni contraseña: usa el secreto de la conexión")
            if parts.scheme == "http" and not _local_host(parts.hostname):
                raise ValueError("url debe usar https (http solo para servidores locales)")
            if self.command or self.args:
                raise ValueError("command y args solo se usan con stdio")
        else:
            if not self.command:
                raise ValueError("command es obligatorio para stdio")
            _plain_filename(self.command, "command")
            if self.url:
                raise ValueError("url solo se usa con streamable_http")
        return self


# Environment variables a capability may never set: they change how programs load code or find files
_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_ENV_FORBIDDEN = {"PATH", "HOME", "TMPDIR", "SHELL", "IFS", "ENV", "BASH_ENV", "SHELLOPTS", "NODE_OPTIONS",
                  "PERL5OPT", "PERL5LIB", "RUBYOPT", "RUBYLIB", "JAVA_TOOL_OPTIONS", "GIT_SSH_COMMAND"}
_ENV_FORBIDDEN_PREFIXES = ("LD_", "DYLD_", "PYTHON", "NODE_", "GCONV", "MALLOC_")
# Variable names that hold credentials (PGPASSWORD, META_TOKEN...): only from the connection's secret
_ENV_CREDENTIAL_WORDS = ("PASSWORD", "PASSWD", "PWD", "SECRET", "TOKEN", "APIKEY", "API_KEY", "CREDENTIAL",
                         "PRIVATE_KEY", "DSN", "COOKIE", "AUTH")


def _env_name(name: str) -> str:
    if not _ENV_NAME.match(name or "") or name in _ENV_FORBIDDEN or name.startswith(_ENV_FORBIDDEN_PREFIXES):
        raise ValueError(f"la variable de entorno {name!r} no está permitida")
    return name


class SecretEnv(BaseModel):
    """One environment variable filled from a field of the capability's connection secret."""
    model_config = ConfigDict(extra="forbid")

    env: str
    field: str = Field(min_length=1, max_length=64)

    @field_validator("env")
    @classmethod
    def allowed(cls, value: str) -> str:
        return _env_name(value)


class CLIConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    executable: str  # looked up in CAPABILITY_BIN_DIR, never a path
    timeout_seconds: int = Field(30, ge=1, le=300)
    memory_mb: int = Field(512, ge=64, le=4096)
    # Plain settings for the program (never secrets: those come from the connection, see env_from_connection)
    env: dict[str, str] = Field(default_factory=dict, max_length=20)
    # Variables filled from the connection secret, e.g. {"env": "PGPASSWORD", "field": "password"}
    env_from_connection: list[SecretEnv] = Field(default_factory=list, max_length=10)
    connection: str | None = Field(None, max_length=100)  # which connection, when it has several
    # Arguments of a harmless run for the health check (e.g. ["--version"]); none: only look for the file
    check_args: list[str] = Field(default_factory=list, max_length=5)

    @field_validator("executable")
    @classmethod
    def plain(cls, value: str) -> str:
        return _plain_filename(value, "executable")

    @field_validator("env")
    @classmethod
    def env_names(cls, value: dict) -> dict:
        for name, setting in value.items():
            _env_name(name)
            if any(word in name for word in _ENV_CREDENTIAL_WORDS):
                raise ValueError(f"env.{name} parece una credencial: ponla en el secreto de la conexión y "
                                 "usa env_from_connection")
            if len(setting) > 500 or "\x00" in setting:
                raise ValueError(f"env.{name}: como máximo 500 caracteres")
        return value

    @field_validator("check_args")
    @classmethod
    def short_check_args(cls, value: list[str]) -> list[str]:
        if any(len(a) > 100 or "\x00" in a for a in value):
            raise ValueError("check_args: cada argumento puede tener como máximo 100 caracteres")
        return value


KIND_CONFIGS = {"mcp": MCPConfig, "cli": CLIConfig}
# Kinds whose config is validated by their own module (prompt tools: capabilities/prompt_tools.py)
KIND_VALIDATORS: dict = {}


def register_validator(kind: str, validator) -> None:
    """validator(config) -> normalised config; raises ValueError."""
    KIND_VALIDATORS[kind] = validator


def _errors(error: ValidationError) -> str:
    return "; ".join(
        (".".join(str(p) for p in e["loc"]) + ": " if e["loc"] else "") + e["msg"].removeprefix("Value error, ")
        for e in error.errors()
    )


def _small(value: dict, limit: int, what: str):
    import json

    if len(json.dumps(value, default=str)) > limit:
        raise ValueError(f"{what} puede tener como máximo {limit} caracteres de JSON")


def validate_config(kind: str, config: dict, config_schema: dict | None = None) -> dict:
    """The capability's config, normalised (defaults filled in) for MCP and CLI; checked against the
    tool's own schema for native tools."""
    if not isinstance(config, dict):
        raise ValueError("config debe ser un objeto")
    if kind in KIND_VALIDATORS:
        return KIND_VALIDATORS[kind](config)
    _small(config, MAX_CONFIG_CHARS, "config")
    reject_secrets(config, "config")
    model = KIND_CONFIGS.get(kind)
    if model is not None:
        try:
            return model.model_validate(config).model_dump(exclude_none=True)
        except ValidationError as e:
            raise ValueError(f"config: {_errors(e)}") from None
    if config_schema is not None:
        try:
            jsonschema.validate(config, config_schema)
        except jsonschema.ValidationError as e:
            raise ValueError(f"config: {e.message}") from None
    return config


class OperationLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timeout_seconds: int | None = Field(None, ge=1, le=300)
    max_output_chars: int | None = Field(None, ge=100, le=200_000)
    max_rows: int | None = Field(None, ge=1, le=100_000)


def validate_limits(limits: dict) -> dict:
    try:
        return OperationLimits.model_validate(limits or {}).model_dump(exclude_none=True)
    except ValidationError as e:
        raise ValueError(f"limits: {_errors(e)}") from None


def validate_arg_schema(schema: dict) -> dict:
    """A JSON Schema for an object (what the model must send). Only checked as a schema here; calls are
    validated against it by the gateway."""
    if not isinstance(schema, dict):
        raise ValueError("arg_schema debe ser un objeto")
    _small(schema, MAX_SCHEMA_CHARS, "arg_schema")
    if schema.get("type") != "object":
        raise ValueError('arg_schema debe describir un objeto ("type": "object")')
    try:
        jsonschema.Draft202012Validator.check_schema(schema)
    except jsonschema.SchemaError as e:
        raise ValueError(f"arg_schema no es un JSON Schema válido: {e.message}") from None
    return schema


# --- CLI command templates (WP6) ---------------------------------------------------------------------------

PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]{0,63})\}")
_TOKEN = re.compile(r"^(?P<prefix>[^{}]*?)(?:\{(?P<name>[A-Za-z_][A-Za-z0-9_]{0,63})\})?$")
MAX_COMMAND_ITEMS = 40
MAX_GROUP_TOKENS = 6
MAX_TOKEN_CHARS = 200
MAX_STRING_ARG = 2000


def parse_token(token: str) -> tuple[str, str | None]:
    """A template token: a literal ("insights"), a whole value ("{account}") or a literal prefix ending in
    "=" followed by a value ("--since={since}"). Returns (prefix, argument name or None)."""
    if not isinstance(token, str) or not token or len(token) > MAX_TOKEN_CHARS or "\x00" in token:
        raise ValueError(f"command: cada parte debe ser un texto de 1 a {MAX_TOKEN_CHARS} caracteres")
    match = _TOKEN.match(token)
    if match is None:
        raise ValueError(f"command: {token!r} no es válido (un valor va solo, '{{x}}', o al final de 'opcion={{x}}')")
    prefix, name = match.group("prefix"), match.group("name")
    if name is not None and prefix and not prefix.endswith("="):
        raise ValueError(f"command: en {token!r} el valor debe ir solo o después de '='")
    return prefix, name


def _argument_rules(name: str, schema: dict) -> None:
    """A value can only reach the command line if its schema bounds it: no free text in a CLI."""
    kind = schema.get("type")
    if kind in ("integer", "number", "boolean"):
        return
    if kind != "string":
        raise ValueError(f"command: el argumento {name!r} debe ser string, integer, number o boolean")
    if "enum" in schema:
        return
    if "pattern" not in schema and schema.get("format") not in ("date", "date-time"):
        raise ValueError(f"command: el argumento {name!r} es texto libre: dale un enum, un pattern o format date")
    if not isinstance(schema.get("maxLength"), int) or schema["maxLength"] > MAX_STRING_ARG:
        raise ValueError(f"command: el argumento {name!r} necesita maxLength (como máximo {MAX_STRING_ARG})")


def validate_command(command: list, arg_schema: dict) -> list:
    """The command template of a CLI operation, checked against its argument schema. Items are tokens
    (see parse_token) or lists of tokens: an optional group, left out when an argument it uses isn't
    given. The first item is a literal (a subcommand: a command position never takes a value), values
    outside groups must be required arguments, and every value's schema must bound it (_argument_rules)."""
    if not isinstance(command, list) or not 1 <= len(command) <= MAX_COMMAND_ITEMS:
        raise ValueError(f"command debe ser una lista de 1 a {MAX_COMMAND_ITEMS} partes")
    properties = (arg_schema or {}).get("properties") or {}
    required = set((arg_schema or {}).get("required") or [])
    for i, item in enumerate(command):
        group = isinstance(item, list)
        tokens = item if group else [item]
        if group and not 1 <= len(tokens) <= MAX_GROUP_TOKENS:
            raise ValueError(f"command: un grupo opcional tiene de 1 a {MAX_GROUP_TOKENS} partes")
        for token in tokens:
            _, name = parse_token(token)
            if name is None:
                continue
            if i == 0:
                raise ValueError("command: la primera parte debe ser fija (el subcomando), no un valor")
            if name not in properties:
                raise ValueError(f"command: {{{name}}} no está en arg_schema.properties")
            if not group and name not in required:
                raise ValueError(f"command: {{{name}}} es opcional: ponlo en un grupo [..] o en required")
            _argument_rules(name, properties[name])
    return command


def validate_connection_config(config: dict) -> dict:
    if not isinstance(config, dict):
        raise ValueError("config debe ser un objeto")
    _small(config, MAX_CONFIG_CHARS, "config")
    reject_secrets(config, "La configuración de la conexión")
    return config


def validate_secret(auth_type: str, secret: dict) -> dict:
    """Text values only, and at least one of the fields the auth type needs."""
    if auth_type not in SECRET_FIELDS:
        raise ValueError(f"Tipo de autenticación desconocido: {auth_type}")
    needed = SECRET_FIELDS[auth_type]
    if not needed:
        raise ValueError("Una conexión sin autenticación no guarda secretos")
    if not isinstance(secret, dict) or not secret or len(secret) > 10:
        raise ValueError("El secreto debe ser un objeto con entre 1 y 10 campos")
    if any(not isinstance(k, str) or not isinstance(v, str) or not v for k, v in secret.items()):
        raise ValueError("Los campos del secreto deben ser textos no vacíos")
    if not any(k in secret for k in needed):
        raise ValueError(f"El secreto de tipo {auth_type} necesita uno de: {', '.join(needed)}")
    return secret
