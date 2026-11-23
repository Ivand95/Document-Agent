"""The one path every capability call goes through.

A user can call an operation only if:
  1. the capability is registered and enabled;
  2. RBAC allows `use` on it (and, if the permission lists `operations` in its constraints, the operation
     is one of them);
  3. the operation is on its whitelist and enabled;
  4. the operation is marked read-only (nothing that changes data outside the app ever runs);
  5. the arguments match the operation's schema.
The same checks build the tool list the model sees (`tools_for`) and run again on every call (`call`),
so a forged tool call fails too. Calls then run with a timeout and an output cap, the output is wrapped
as untrusted data, and the call is written to the audit log (operation and arguments, never the output).

The data-class check against the model that will read the output (WP4b) plugs in here as step 6.
"""
import asyncio
import hashlib
import inspect
import json
import logging
import os
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import jsonschema

from capabilities import native, registry
from capabilities.registry import Capability, Operation
from core.prompt_safety import wrap_untrusted
from rbac import audit
from rbac.model import Access

log = logging.getLogger("capabilities")

DEFAULT_TIMEOUT_SECONDS = int(os.getenv("CAPABILITY_TIMEOUT_SECONDS", "30"))
DEFAULT_MAX_OUTPUT_CHARS = int(os.getenv("CAPABILITY_MAX_OUTPUT_CHARS", "50000"))
AUDIT_ARG_CHARS = 500  # longer argument values are cut in the audit log
AUDIT_ARGS_TOTAL_CHARS = 4000
PRIVATE = "x-private"  # an argument schema property with "x-private": true is never written to the audit log
TOOL_NAME = re.compile(r"[^A-Za-z0-9_-]")

MESSAGES = {
    "not_found": "Esa herramienta no existe.",
    "disabled": "Esa herramienta está desactivada.",
    "denied": "No tienes permiso para usar esa herramienta.",
    "not_whitelisted": "Esa operación no está habilitada.",
    "not_read_only": "Esa operación puede modificar datos y no está permitida.",
    "bad_arguments": "Los argumentos no son válidos para esa operación.",
    "not_available": "Esa herramienta todavía no se puede ejecutar.",
    "timeout": "La herramienta tardó demasiado en responder.",
    "failed": "La herramienta falló. Intenta de nuevo más tarde.",
}


class CapabilityError(Exception):
    """A refused or failed call. `message` can be shown to the user (or the model); `detail` is for logs."""

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.message = MESSAGES.get(code, MESSAGES["failed"])
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


@dataclass(frozen=True)
class ToolSpec:
    """An operation the user may call, as the model sees it."""

    name: str  # unique, OpenAI-compatible (^[A-Za-z0-9_-]{1,64}$)
    capability_key: str
    operation: str
    description: str
    parameters: dict
    data_class: str

    def as_openai_tool(self) -> dict:
        return {"type": "function",
                "function": {"name": self.name, "description": self.description, "parameters": self.parameters}}


@dataclass(frozen=True)
class CallResult:
    capability_key: str
    operation: str
    data_class: str
    text: str  # wrapped as untrusted data, ready for the model
    output_chars: int
    truncated: bool
    duration_ms: int


@dataclass(frozen=True)
class Authorized:
    capability: Capability
    operation: Operation
    constraints: dict


# --- Checks ---------------------------------------------------------------------------------------------


def authorize(access: Access, capability_key: str, operation: str, snap=None) -> Authorized:
    """Checks 1–4. Raises CapabilityError."""
    snap = snap or registry.snapshot()
    capability = snap.get(capability_key)
    if capability is None:
        raise CapabilityError("not_found", capability_key)
    if not capability.enabled:
        raise CapabilityError("disabled", capability_key)
    decision = access.check("use", "capability", capability_key)
    if not decision.allowed:
        raise CapabilityError("denied", decision.reason)
    allowed_ops = decision.constraints.get("operations")
    if allowed_ops is not None and operation not in allowed_ops:
        raise CapabilityError("denied", f"operation {operation} not in the permission's operations")
    op = capability.operation(operation)
    if op is None or not op.enabled:
        raise CapabilityError("not_whitelisted", f"{capability_key}.{operation}")
    if not op.read_only:
        raise CapabilityError("not_read_only", f"{capability_key}.{operation}")
    return Authorized(capability, op, decision.constraints)


def validate_args(op: Operation, args) -> dict:
    """Check 5."""
    if not isinstance(args, dict):
        raise CapabilityError("bad_arguments", "arguments must be an object")
    try:
        jsonschema.validate(args, op.arg_schema)
    except jsonschema.ValidationError as e:
        raise CapabilityError("bad_arguments", e.message[:300]) from None
    except jsonschema.SchemaError as e:
        raise CapabilityError("failed", f"bad stored schema: {e.message[:300]}") from None
    return args


def _tool_name(key: str, operation: str, taken: set) -> str:
    name = TOOL_NAME.sub("_", f"{key}__{operation}")[:64]
    if name in taken:
        suffix = hashlib.sha1(f"{key}/{operation}".encode()).hexdigest()[:8]
        name = f"{name[:55]}_{suffix}"
    taken.add(name)
    return name


def _model_schema(schema: dict) -> dict:
    """The argument schema without our private annotations."""
    if isinstance(schema, dict):
        return {k: _model_schema(v) for k, v in schema.items() if k != PRIVATE}
    if isinstance(schema, list):
        return [_model_schema(v) for v in schema]
    return schema


def tools_for(access: Access) -> list[ToolSpec]:
    """Every operation this user may call now, for the model's tool list."""
    snap = registry.snapshot()
    specs, taken = [], set()
    for key in sorted(snap.capabilities):
        capability = snap.capabilities[key]
        if capability.kind not in EXECUTORS:
            continue
        for op in capability.operations:
            try:
                authorize(access, key, op.operation, snap)
            except CapabilityError:
                continue
            description = op.description or capability.description
            specs.append(ToolSpec(
                name=_tool_name(key, op.operation, taken), capability_key=key, operation=op.operation,
                description=f"{capability.name}: {description}"[:1024], parameters=_model_schema(op.arg_schema),
                data_class=capability.data_class,
            ))
    return specs


def allowed_capabilities(access: Access) -> list[str]:
    """Keys of the enabled capabilities this user may use (whatever their operations)."""
    snap = registry.snapshot()
    return [k for k, c in sorted(snap.capabilities.items()) if c.enabled and access.can("use", "capability", k)]


# --- Running ----------------------------------------------------------------------------------------------

Executor = Callable[[Capability, Operation, dict, native.ToolContext], Awaitable[object]]


async def _run_native(capability: Capability, op: Operation, args: dict, ctx: native.ToolContext):
    tool = native.get(capability.key)
    native_op = tool.operation(op.operation) if tool else None
    if native_op is None:
        raise CapabilityError("not_available", f"no code for {capability.key}.{op.operation}")
    if not native_op.read_only:  # the code has the last word, whatever the table says
        raise CapabilityError("not_read_only", f"{capability.key}.{op.operation}")
    if inspect.iscoroutinefunction(native_op.handler):
        return await native_op.handler(args, ctx)
    return await asyncio.to_thread(native_op.handler, args, ctx)


# How each kind runs. MCP (WP5), CLI (WP6), prompt and code tools (WP4b) register theirs.
EXECUTORS: dict[str, Executor] = {"tool": _run_native}


def register_executor(kind: str, executor: Executor) -> None:
    EXECUTORS[kind] = executor


def _secret_reader(capability: Capability):
    def read(name: str | None = None) -> dict:
        connections = [c for c in registry.list_connections(capability.id) if c["status"] != "disabled"]
        if name is not None:
            connections = [c for c in connections if c["name"] == name]
        if len(connections) != 1:
            raise CapabilityError("not_available", f"{capability.key}: expected one connection named {name!r}")
        secret = registry.read_secret(connections[0]["id"])
        if secret is None:
            raise CapabilityError("not_available", f"{capability.key}: connection without a secret")
        return secret

    return read


def _as_text(output) -> str:
    if isinstance(output, str):
        return output
    return json.dumps(output, ensure_ascii=False, default=str)


def audit_args(op: Operation, args: dict) -> dict:
    """The arguments as written to the audit log: private ones hidden, long values cut, size capped."""
    properties = (op.arg_schema or {}).get("properties") or {}
    shown = {}
    for key, value in args.items():
        if (properties.get(key) or {}).get(PRIVATE):
            shown[key] = "[oculto]"
            continue
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
        shown[key] = text if len(text) <= AUDIT_ARG_CHARS else text[:AUDIT_ARG_CHARS] + "…"
    if len(json.dumps(shown, ensure_ascii=False)) > AUDIT_ARGS_TOTAL_CHARS:
        return {"_omitted": f"{len(args)} argumentos, demasiado largos para el registro"}
    return shown


async def call(access: Access, claims: dict, capability_key: str, operation: str, args) -> CallResult:
    """Runs one operation for the user, or raises CapabilityError. Every outcome is audited."""
    started = time.monotonic()
    details = {"operation": operation}
    try:
        authorized = authorize(access, capability_key, operation)
        capability, op = authorized.capability, authorized.operation
        details["args"] = audit_args(op, validate_args(op, args))
        executor = EXECUTORS.get(capability.kind)
        if executor is None:
            raise CapabilityError("not_available", f"no executor for kind {capability.kind}")
        limits = {**op.limits}
        if "max_rows" in authorized.constraints:  # a permission can lower (never raise) the row limit
            limits["max_rows"] = min(limits.get("max_rows", authorized.constraints["max_rows"]),
                                     authorized.constraints["max_rows"])
        timeout = limits.get("timeout_seconds") or capability.config.get("timeout_seconds") or DEFAULT_TIMEOUT_SECONDS
        ctx = native.ToolContext(user_id=access.user_id, capability_key=capability_key, operation=operation,
                                 config=capability.config, limits=limits, secret=_secret_reader(capability))
        try:
            output = await asyncio.wait_for(executor(capability, op, args, ctx), timeout=timeout)
        except asyncio.TimeoutError:
            raise CapabilityError("timeout", f"after {timeout}s") from None
        except CapabilityError:
            raise
        except Exception as e:
            log.exception("Capability %s.%s failed", capability_key, operation)
            raise CapabilityError("failed", type(e).__name__) from None

        text = _as_text(output)
        max_chars = limits.get("max_output_chars") or DEFAULT_MAX_OUTPUT_CHARS
        truncated = len(text) > max_chars
        if truncated:
            text = text[:max_chars] + "\n[... salida recortada ...]"
        wrapped = wrap_untrusted(
            "tool_result", "output", [({"capability": capability_key, "operation": operation}, text)]
        )
        duration = int((time.monotonic() - started) * 1000)
        details.update({"duration_ms": duration, "output_chars": len(text), "truncated": truncated})
        audit.record(claims, "capability.call", "capability", capability_key, details=details)
        return CallResult(capability_key, operation, capability.data_class, wrapped, len(text), truncated, duration)
    except CapabilityError as e:
        details.update({"error": e.code, "duration_ms": int((time.monotonic() - started) * 1000)})
        outcome = "denied" if e.code in ("not_found", "disabled", "denied", "not_whitelisted", "not_read_only",
                                         "bad_arguments") else "error"
        # Argument errors can quote the values (which may be the user's text): log only the code then
        log.warning("Capability call %s.%s refused or failed: %s", capability_key, operation,
                    e.code if e.code == "bad_arguments" else e)
        audit.record(claims, "capability.call", "capability", capability_key, outcome=outcome, details=details)
        raise
