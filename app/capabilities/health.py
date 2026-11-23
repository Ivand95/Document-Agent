"""Connection health checks shown in the panel: is the secret readable, and does the service answer?

Each kind can register a checker (MCP in WP5, CLI in WP6); native tools can define `check` themselves.
Without a checker, only the stored secret is verified and the status stays 'unchecked'.
"""
import asyncio
import logging
from collections.abc import Callable

from capabilities import native, registry, secrets
from capabilities.catalog import SECRET_FIELDS

log = logging.getLogger("capabilities")

CHECK_TIMEOUT_SECONDS = 20

# kind -> (capability row, connection row, decrypted secret or None) -> detail; raises if broken
Checker = Callable[[dict, dict, dict | None], str]
CHECKERS: dict[str, Checker] = {}


def register_checker(kind: str, checker: Checker) -> None:
    CHECKERS[kind] = checker


def _native_checker(capability: dict, connection: dict, secret: dict | None) -> str | None:
    tool = native.get(capability["key"])
    if tool is None:
        raise RuntimeError("La herramienta ya no existe en el código")
    if tool.check is None:
        return None
    return tool.check(capability.get("config") or {}, secret)


def _probe(capability: dict | None, connection: dict) -> tuple[str, str]:
    """(status, detail) without writing anything."""
    if connection["status"] == "disabled":
        return "disabled", "Conexión desactivada"
    try:
        secret = registry.read_secret(connection["id"])
    except secrets.SecretsUnavailable as e:
        return "error", str(e)
    except secrets.SecretUnreadable:
        return "error", "No se pudo descifrar el secreto (¿cambió CAPABILITY_SECRET_KEY?). Vuelve a guardarlo."
    if SECRET_FIELDS.get(connection["auth_type"]) and secret is None:
        return "error", "Falta el secreto"
    if capability is None:
        return "unchecked", "Secreto guardado; sin prueba de conexión para este uso"
    checker = _native_checker if capability["kind"] == "tool" else CHECKERS.get(capability["kind"])
    try:
        detail = checker(capability, connection, secret) if checker else None
    except Exception as e:
        # The message may come from a remote service: keep it short, never the secret
        log.warning("Health check of connection %s failed: %s", connection["id"], type(e).__name__)
        return "error", (str(e) or type(e).__name__)[:300]
    if detail is None:
        return "unchecked", "Secreto correcto; todavía no hay prueba de conexión para este tipo"
    return "ok", detail or "Conexión correcta"


async def check_connection(connection: dict) -> dict:
    """Runs the check, stores the result and returns the updated connection."""
    capability = registry.get_capability(connection["capability_id"]) if connection.get("capability_id") else None
    try:
        status, detail = await asyncio.wait_for(
            asyncio.to_thread(_probe, capability, connection), timeout=CHECK_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        status, detail = "error", f"Sin respuesta en {CHECK_TIMEOUT_SECONDS} s"
    return registry.record_check(connection["id"], status, detail)
