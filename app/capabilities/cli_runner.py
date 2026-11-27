"""CLI capabilities (WP6): a registered executable whose whitelisted operations are command templates.

An admin registers the executable (a name in CAPABILITY_BIN_DIR) and, per operation, an argument schema
and a command template (capabilities/configs.py validate_command): fixed subcommand first, values only
where the template puts them, every value bounded by its schema (enum, pattern or date; numbers;
booleans). A call, after the gateway's RBAC, whitelist, read-only and schema checks:
  1. the capability's read-only guard (capabilities/guards.py);
  2. the argument list is built from the template. A value standing alone as an argument may not start
     with "-" (it would be read as an option: use "--opt={x}"), and no value carries control characters;
  3. the program runs in the sandbox (capabilities/sandbox.py) with its settings and only the secrets
     its config maps from the connection;
  4. a non-zero exit is an error (stderr is never returned: it is logged by size only); the output is
     refused whole if it carries text aimed at steering the model.
"""
import asyncio
import logging
import re

from capabilities import gateway, guards, health, sandbox
from capabilities.configs import parse_token
from capabilities.gateway import CapabilityError
from capabilities.registry import Capability, Operation
from core.prompt_safety import find_injection

log = logging.getLogger("capabilities")

KIND = "cli"
CONTROL = re.compile(r"[\x00-\x1f\x7f]")
DEFAULT_MAX_OUTPUT_BYTES = 1_000_000
TOOL_ERROR = "El comando terminó con un error."
UNSAFE = "La salida del comando contenía instrucciones dirigidas al asistente y se descartó."


def _value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        if CONTROL.search(value):
            raise CapabilityError("bad_arguments", "control characters in a value")
        return value
    raise CapabilityError("bad_arguments", "only text, numbers and booleans reach a command line")


def build_args(command, args: dict) -> list[str]:
    """The arguments (after the executable) for these validated args. Optional groups whose values are
    missing are left out."""
    argv = []
    for item in command:
        tokens = item if isinstance(item, (list, tuple)) else [item]
        group = isinstance(item, (list, tuple))
        rendered = []
        for token in tokens:
            prefix, name = parse_token(token)
            if name is None:
                rendered.append(prefix)
                continue
            if args.get(name) is None:
                if group:
                    rendered = None
                    break
                raise CapabilityError("bad_arguments", f"missing {name}")
            value = _value(args[name])
            if not prefix and value.startswith("-"):
                raise CapabilityError("bad_arguments", f"{name} starts with '-'")
            rendered.append(prefix + value)
        if rendered is not None:
            argv += rendered
    return argv


def environment(capability: Capability, ctx) -> dict:
    config = capability.config or {}
    env = dict(config.get("env") or {})
    mapped = config.get("env_from_connection") or []
    if mapped:
        secret = ctx.secret(config.get("connection"))
        for entry in mapped:
            if entry["field"] not in secret:
                raise CapabilityError("not_available", f"{capability.key}: the secret has no {entry['field']!r}")
            env[entry["env"]] = secret[entry["field"]]
    return sandbox.base_env(env)


async def run(capability: Capability, op: Operation, args: dict, ctx) -> str:
    guards.check(capability, op, args)
    if not op.command:
        raise CapabilityError("not_available", f"{capability.key}.{op.operation} has no command")
    config = capability.config or {}
    argv = [sandbox.resolve_executable(config["executable"]), *build_args(op.command, args)]
    env = await asyncio.to_thread(environment, capability, ctx)
    timeout = ctx.limits.get("timeout_seconds") or config.get("timeout_seconds") or 30
    max_bytes = max(DEFAULT_MAX_OUTPUT_BYTES, 4 * (ctx.limits.get("max_output_chars") or 0))
    result = await sandbox.run(argv, env=env, timeout=timeout, memory_mb=config.get("memory_mb") or 512,
                               max_stdout=max_bytes)
    if result.timed_out:
        raise CapabilityError("timeout", f"after {timeout}s")
    if result.exit_code != 0 and not result.truncated:
        log.warning("CLI %s.%s exited with %s (%d bytes of stderr)", capability.key, op.operation,
                    result.exit_code, len(result.stderr))
        raise CapabilityError("tool_error", f"exit {result.exit_code}", message=TOOL_ERROR)
    text = result.stdout.decode("utf-8", errors="replace")
    if result.truncated:
        text += "\n[... salida recortada ...]"
    rule = find_injection(text, strong_only=True)
    if rule:
        log.warning("CLI %s.%s output dropped: possible injected instructions (rule %s)",
                    capability.key, op.operation, rule)
        raise CapabilityError("unsafe_output", rule, message=UNSAFE)
    return text


gateway.register_executor(KIND, run)
gateway.MESSAGES.setdefault("tool_error", TOOL_ERROR)
gateway.MESSAGES.setdefault("unsafe_output", UNSAFE)


def _check(capability_row: dict, connection: dict, secret: dict | None) -> str:
    """Health check (worker thread): the executable is there and, with check_args, runs and exits 0."""
    config = capability_row.get("config") or {}
    try:
        path = sandbox.resolve_executable(config.get("executable") or "")
        if not config.get("check_args"):
            return "Ejecutable encontrado (sin prueba: añade check_args para ejecutarlo)"
        env = sandbox.base_env(dict(config.get("env") or {}))
        result = asyncio.run(sandbox.run([path, *config["check_args"]], env=env,
                                         timeout=min(int(config.get("timeout_seconds") or 30), 20),
                                         memory_mb=config.get("memory_mb") or 512, max_stdout=4096))
    except CapabilityError as e:
        raise RuntimeError(e.detail or e.message) from None
    if result.timed_out or result.exit_code != 0:
        raise RuntimeError("El comando de prueba falló" + ("" if result.timed_out else f" (código {result.exit_code})"))
    return "Comando disponible"


health.register_checker(KIND, _check)
