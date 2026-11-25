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
    command: str | None = None  # stdio: executable name, looked up in the allowlisted directory (WP5/WP6)
    args: list[str] = Field(default_factory=list, max_length=20)
    timeout_seconds: int = Field(30, ge=1, le=300)

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


class CLIConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    executable: str  # looked up in the server's allowlisted directory (WP6), never a path
    timeout_seconds: int = Field(30, ge=1, le=300)

    @field_validator("executable")
    @classmethod
    def plain(cls, value: str) -> str:
        return _plain_filename(value, "executable")


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
