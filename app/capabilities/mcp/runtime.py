"""Running whitelisted MCP tools, discovering a server's tools into the whitelist, and the health check.

A call (after the gateway's RBAC, whitelist, read-only and argument checks):
  1. the capability's read-only guard (capabilities/guards.py);
  2. the server's rate limit (config rate_per_minute, per process): over it, the call is refused;
  3. tools/call with the timeout, retried twice with backoff on transport failures (never on an
     answer: a tool error or a protocol error is final);
  4. the answer as text (structured content as JSON; images and other binary content left out),
     refused whole if it carries text aimed at steering the model (prompt_safety, strong patterns).
The gateway then caps its size, wraps it as untrusted data and audits the call.

Discovery copies tools/list into capability_operations. New tools start disabled and not read-only. A
whitelisted tool whose description or schema changed upstream is disabled until an admin looks at it
again (a changed description can carry new instructions), and so is one the server stopped listing.
"""
import asyncio
import json
import logging
import random
import re
import threading
import time
from collections import defaultdict, deque

from mcp.shared.exceptions import MCPError

from capabilities import gateway, guards, health, registry
from capabilities.catalog import OPERATION_PATTERN
from capabilities.configs import validate_arg_schema
from capabilities.gateway import CapabilityError
from capabilities.mcp import transport
from capabilities.mcp.oauth import NeedsReconnect, RECONNECT
from capabilities.registry import Capability, Operation
from core.prompt_safety import find_injection

log = logging.getLogger("capabilities")

KIND = "mcp"
RETRIES = 2
BACKOFF_SECONDS = 0.5
MAX_PAGES = 20
MAX_DESCRIPTION = 2000
UNSAFE = "La respuesta de la herramienta contenía instrucciones dirigidas al asistente y se descartó."
TOOL_ERROR = "La herramienta respondió con un error."
RATE_LIMITED = "Se alcanzó el límite de llamadas a esa herramienta. Intenta de nuevo en un minuto."
_OPERATION = re.compile(OPERATION_PATTERN)


# --- Rate limit -----------------------------------------------------------------------------------------


class RateLimiter:
    """Calls per minute per key, in this process (a sliding window)."""

    def __init__(self):
        self._calls: dict = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, per_minute: int) -> bool:
        now = time.monotonic()
        with self._lock:
            calls = self._calls[key]
            while calls and now - calls[0] >= 60:
                calls.popleft()
            if len(calls) >= per_minute:
                return False
            calls.append(now)
            return True


limiter = RateLimiter()


# --- Answers ----------------------------------------------------------------------------------------


def as_text(result) -> str:
    """The text blocks (servers put the serialized result there too); the structured content as JSON
    only when there is no text."""
    has_text = any(getattr(b, "type", "") == "text" for b in result.content or [])
    if result.structured_content is not None and not has_text:
        return json.dumps(result.structured_content, ensure_ascii=False, default=str)
    parts = []
    for block in result.content or []:
        kind = getattr(block, "type", "")
        if kind == "text":
            parts.append(block.text)
        elif kind == "resource" and getattr(getattr(block, "resource", None), "text", None) is not None:
            parts.append(block.resource.text)
        else:
            parts.append(f"[contenido {kind or 'desconocido'} omitido]")
    return "\n\n".join(parts)


def _transient(error: BaseException) -> bool:
    """Worth another try: the connection failed or timed out. An answer from the server never is."""
    if isinstance(error, (MCPError, CapabilityError, NeedsReconnect)):
        return False
    if isinstance(error, BaseExceptionGroup):
        return all(_transient(e) for e in error.exceptions)
    return isinstance(error, (OSError, TimeoutError, asyncio.TimeoutError)) or type(error).__module__.startswith(
        ("httpx2", "httpcore2", "anyio"))


def _unwrap(error: BaseException) -> BaseException:
    while isinstance(error, BaseExceptionGroup) and len(error.exceptions) == 1:
        error = error.exceptions[0]
    return error


async def call_tool(capability: Capability, operation: str, args: dict, timeout: float):
    target = await asyncio.to_thread(transport.target_for, capability)
    for attempt in range(RETRIES + 1):
        try:
            async with transport.open_client(target, timeout=timeout) as client:
                return await client.call_tool(operation, args, read_timeout_seconds=timeout)
        except BaseException as raw:  # task groups wrap errors in ExceptionGroups
            if isinstance(raw, (KeyboardInterrupt, SystemExit, asyncio.CancelledError)):
                raise
            error = _unwrap(raw)
            if isinstance(error, NeedsReconnect):
                if target.connection:
                    await asyncio.to_thread(registry.record_check, target.connection["id"], "error", RECONNECT)
                raise CapabilityError("not_available", "oauth: needs reconnect", message=RECONNECT) from None
            if isinstance(error, CapabilityError):
                raise error from None
            if attempt == RETRIES or not _transient(raw):
                raise CapabilityError("failed", f"mcp: {type(error).__name__}") from None
            log.warning("MCP %s.%s attempt %d failed (%s); retrying", capability.key, operation, attempt + 1,
                        type(error).__name__)
            await asyncio.sleep(BACKOFF_SECONDS * (2 ** attempt) * (1 + random.random()))


async def run(capability: Capability, op: Operation, args: dict, ctx) -> str:
    guards.check(capability, op, args)
    if not limiter.allow(capability.key, int(capability.config.get("rate_per_minute") or 60)):
        raise CapabilityError("rate_limited", capability.key, message=RATE_LIMITED)
    timeout = ctx.limits.get("timeout_seconds") or capability.config.get("timeout_seconds") or 30
    result = await call_tool(capability, op.operation, args, timeout)
    if result.is_error:
        raise CapabilityError("tool_error", f"{capability.key}.{op.operation} returned an error", message=TOOL_ERROR)
    text = as_text(result)
    rule = find_injection(text, strong_only=True)
    if rule:
        log.warning("MCP %s.%s answer dropped: possible injected instructions (rule %s)",
                    capability.key, op.operation, rule)
        raise CapabilityError("unsafe_output", rule, message=UNSAFE)
    return text


gateway.register_executor(KIND, run)
gateway.MESSAGES.setdefault("rate_limited", RATE_LIMITED)
gateway.MESSAGES.setdefault("tool_error", TOOL_ERROR)
gateway.MESSAGES.setdefault("unsafe_output", UNSAFE)


# --- Discovery ---------------------------------------------------------------------------------------


async def list_tools(capability: Capability, timeout: float = 60, pages: int = MAX_PAGES) -> list:
    target = await asyncio.to_thread(transport.target_for, capability)
    tools, cursor = [], None
    try:
        async with transport.open_client(target, timeout=timeout) as client:
            for _ in range(pages):
                page = await client.list_tools(cursor=cursor)
                tools += page.tools
                cursor = page.next_cursor
                if not cursor:
                    break
    except BaseException as raw:
        if isinstance(raw, (KeyboardInterrupt, SystemExit, asyncio.CancelledError)):
            raise
        error = _unwrap(raw)
        if isinstance(error, NeedsReconnect):
            raise CapabilityError("not_available", "oauth: needs reconnect", message=RECONNECT) from None
        if isinstance(error, CapabilityError):
            raise error from None
        raise CapabilityError("failed", f"mcp list: {type(error).__name__}",
                              message="No se pudo leer la lista de herramientas del servidor.") from None
    return tools


def _hints(tool) -> dict:
    a = tool.annotations
    return {} if a is None else {k: v for k, v in (("read_only_hint", a.read_only_hint),
                                                    ("destructive_hint", a.destructive_hint)) if v is not None}


def sync_operations(capability_row: dict, tools: list, by: str) -> dict:
    """Copies the listed tools into the whitelist (see the module docstring). Returns a report; the
    server's read-only/destructive hints are reported for the admin, never trusted."""
    current = {o["operation"]: o for o in registry.list_operations(capability_row["id"])}
    report = {"found": len(tools), "added": [], "changed": [], "disabled": [], "missing": [], "skipped": [],
              "hints": {}}
    listed = set()
    for tool in tools:
        name = tool.name
        if not isinstance(name, str) or not _OPERATION.match(name):
            report["skipped"].append({"tool": str(name)[:100], "reason": "nombre no válido"})
            continue
        try:
            schema = validate_arg_schema(tool.input_schema or {"type": "object"})
        except ValueError as e:
            report["skipped"].append({"tool": name, "reason": str(e)[:200]})
            continue
        listed.add(name)
        description = (tool.description or tool.title or "")[:MAX_DESCRIPTION]
        if _hints(tool):
            report["hints"][name] = _hints(tool)
        existing = current.get(name)
        if existing is None:
            registry.add_operation(capability_row["id"], name, description, schema, {}, False, by)
            report["added"].append(name)
        elif existing.get("description") != description or existing.get("arg_schema") != schema:
            changes = {"description": description, "arg_schema": schema}
            if existing.get("enabled"):
                changes["enabled"] = False
                report["disabled"].append(name)
            registry.update_operation(capability_row["id"], name, changes, by)
            report["changed"].append(name)
    for name, existing in current.items():
        if name not in listed:
            report["missing"].append(name)
            if existing.get("enabled"):
                registry.update_operation(capability_row["id"], name, {"enabled": False}, by)
                report["disabled"].append(name)
    return report


async def discover(capability_row: dict, by: str) -> dict:
    capability = registry._capability(capability_row)
    tools = await list_tools(capability)
    return await asyncio.to_thread(sync_operations, capability_row, tools, by)


# --- Health check ----------------------------------------------------------------------------------------


def _check(capability_row: dict, connection: dict, secret: dict | None) -> str:
    """Runs in a worker thread (health.check_connection): connects and lists the first page of tools."""
    capability = registry._capability(capability_row)
    try:
        tools = asyncio.run(list_tools(capability, timeout=15, pages=1))
    except CapabilityError as e:
        raise RuntimeError(e.message) from None
    return f"Conexión correcta: {len(tools)} herramientas"


health.register_checker(KIND, _check)
