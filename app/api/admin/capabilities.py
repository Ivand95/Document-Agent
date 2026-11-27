"""Admin API for the capability registry: capabilities, their operation whitelist and their connections.

Everything needs `admin` on the `capabilities` admin area. On top of that:
- only a super_admin sets a data class below `sensitive`, or lowers a capability's data class (it decides
  which LLM models may read the output, WP4b);
- only a super_admin marks an operation read-only on a capability whose risk is `write` or `financial`;
- native tools come from code: they can't be created or deleted here, and their operations' schema,
  description and read-only flag can't be edited (only enabled and limits);
- MCP and CLI capabilities are created here; skills, prompt tools and code tools have their own flows
  (WP13, WP4b) and can only be enabled/disabled here (a prompt tool only after a successful test run of
  its current version, see /admin/prompt-tools);
- secrets are write-only: stored encrypted, never returned, never written to the audit log;
- MCP servers: POST /{id}/discover copies the server's tools into the whitelist (new ones disabled);
  an OAuth connection is authorized with POST /connections/{id}/oauth/start (WP5).
Who may *use* a capability is set in the permission matrix (/admin/rbac, resource_type "capability").
"""
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from postgrest.exceptions import APIError

from api.deps import get_access, require
from capabilities import cli_runner  # noqa: F401  (registers the CLI executor and health check)
from capabilities import health, native, prompt_tools, registry, secrets
from capabilities.gateway import CapabilityError
from capabilities.mcp import oauth
from capabilities.mcp import runtime as mcp_runtime
from capabilities.mcp.transport import Target
from core.security import normalize_origin, origin_allowed
from capabilities.catalog import AUTH_TYPES, CREATABLE_KINDS, DATA_CLASSES, KINDS, RISKS, data_class_rank
from models.capabilities import (
    CapabilityCreate, CapabilityUpdate, ConnectionCreate, ConnectionUpdate, OAuthStart, OperationCreate,
    OperationUpdate, SecretSet,
)
from rbac import audit
from rbac.model import Access

log = logging.getLogger("capabilities")

router = APIRouter(prefix="/admin/capabilities")

require_capabilities_admin = require("admin", "admin_area", "capabilities")

ONLY_SUPER_ADMIN = "Solo un super administrador puede hacer este cambio."
FROM_CODE = "Las herramientas nativas vienen del código: esto no se puede cambiar aquí."
OWN_EDITOR = "Este tipo de capacidad se configura en su propio editor."


def by(claims: dict) -> str:
    return claims.get("sub") or claims.get("user_id") or "?"


def db_errors(error: APIError):
    code = getattr(error, "code", None)
    if code == "23505":
        raise HTTPException(status_code=409, detail="Ya existe.")
    if code == "23503":
        raise HTTPException(status_code=404, detail="No existe.")
    if code in ("23514", "22P02"):
        raise HTTPException(status_code=422, detail="Valor no válido.")
    raise error


def invalid(error: ValueError):
    raise HTTPException(status_code=422, detail=str(error))


def capability_or_404(capability_id) -> dict:
    capability = registry.get_capability(capability_id)
    if capability is None:
        raise HTTPException(status_code=404, detail="Capacidad no encontrada.")
    return capability


def connection_or_404(connection_id) -> dict:
    """Only connections of a capability: provider connections (no capability) belong to the models area."""
    connection = registry.get_connection(connection_id)
    if connection is None or connection.get("capability_id") is None:
        raise HTTPException(status_code=404, detail="Conexión no encontrada.")
    return connection


def guard_data_class(actor: Access, new: str, current: str | None = None):
    lowered = data_class_rank(new) < data_class_rank(current or "sensitive")
    if lowered and not actor.is_super_admin:
        raise HTTPException(status_code=403, detail=ONLY_SUPER_ADMIN)


def with_details(capability: dict) -> dict:
    result = dict(capability)
    result["operations"] = registry.list_operations(capability["id"])
    result["connections"] = registry.list_connections(capability["id"])
    if capability["kind"] == "tool":
        result["in_code"] = native.get(capability["key"]) is not None
    return result


# --- Catalog and sync ---------------------------------------------------------------------------------------


@router.get("/catalog")
def catalog_endpoint(_claims: dict = Depends(require_capabilities_admin)):
    return {
        "kinds": list(KINDS), "creatable_kinds": list(CREATABLE_KINDS), "risks": list(RISKS),
        "data_classes": list(DATA_CLASSES), "auth_types": list(AUTH_TYPES),
        "secrets_configured": secrets.configured(),
    }


@router.post("/sync-native")
def sync_native_endpoint(claims: dict = Depends(require_capabilities_admin)):
    """Copies the native tools in the code into the registry (also done at start-up)."""
    report = registry.sync_native_tools(by=by(claims))
    audit.record(claims, "capability.sync_native", "capability", None, details=report)
    return report


# --- Capabilities -----------------------------------------------------------------------------------------


@router.get("")
def list_endpoint(kind: str | None = Query(None), _claims: dict = Depends(require_capabilities_admin)):
    if kind is not None and kind not in KINDS:
        raise HTTPException(status_code=422, detail="Tipo desconocido.")
    capabilities = registry.list_capabilities(kind)
    counts: dict = {}
    snap = registry.snapshot()
    for cap in snap.capabilities.values():
        counts[cap.id] = (sum(1 for o in cap.operations if o.enabled), len(cap.operations))
    for cap in capabilities:
        cap["operations_enabled"], cap["operations_total"] = counts.get(cap["id"], (0, 0))
    return {"capabilities": capabilities}


@router.get("/{capability_id}")
def get_endpoint(capability_id: uuid.UUID, _claims: dict = Depends(require_capabilities_admin)):
    return with_details(capability_or_404(capability_id))


@router.post("", status_code=201)
def create_endpoint(
    request: CapabilityCreate, claims: dict = Depends(require_capabilities_admin), actor: Access = Depends(get_access)
):
    """New capabilities start disabled, with no operations (MCP: discovered in WP5; CLI: added by hand)."""
    guard_data_class(actor, request.data_class)
    try:
        capability = registry.create_capability(
            request.key, request.kind, request.name, request.description, request.risk, request.data_class,
            request.config, by(claims),
        )
    except ValueError as e:
        invalid(e)
    except APIError as e:
        db_errors(e)
    audit.record(claims, "capability.create", "capability", capability["key"],
                 details={"kind": request.kind, "risk": request.risk, "data_class": request.data_class,
                          "config": capability["config"]})
    return capability


@router.patch("/{capability_id}")
def update_endpoint(
    capability_id: uuid.UUID, request: CapabilityUpdate,
    claims: dict = Depends(require_capabilities_admin), actor: Access = Depends(get_access),
):
    capability = capability_or_404(capability_id)
    changes = request.model_dump(exclude_none=True)
    if not changes:
        return capability
    own_editor = capability["kind"] in ("skill", "prompt_tool", "code_tool")
    if own_editor and ("config" in changes or ("data_class" in changes and capability["kind"] != "skill")):
        raise HTTPException(status_code=400, detail=OWN_EDITOR)
    if "data_class" in changes:
        guard_data_class(actor, changes["data_class"], capability["data_class"])
    if changes.get("enabled") and not capability["enabled"] and capability["kind"] == "prompt_tool":
        try:
            prompt_tools.ready_to_enable(capability)
        except prompt_tools.NotReady as e:
            raise HTTPException(status_code=409, detail=str(e))
    if changes.get("enabled") and capability["kind"] == "tool" and native.get(capability["key"]) is None:
        raise HTTPException(status_code=409, detail="Esta herramienta ya no existe en el código.")
    try:
        updated = registry.update_capability(capability, changes, by(claims))
    except ValueError as e:
        invalid(e)
    except APIError as e:
        db_errors(e)
    audit.record(claims, "capability.update", "capability", capability["key"],
                 details={"changes": changes, "before": {k: capability.get(k) for k in changes}})
    return updated


@router.delete("/{capability_id}", status_code=204)
def delete_endpoint(capability_id: uuid.UUID, claims: dict = Depends(require_capabilities_admin)):
    """Also deletes its operations, connections (and secrets) and every permission on it."""
    capability = capability_or_404(capability_id)
    if capability["kind"] == "tool" and native.get(capability["key"]) is not None:
        raise HTTPException(status_code=400, detail=FROM_CODE + " Desactívala en su lugar.")
    registry.delete_capability(capability_id)
    audit.record(claims, "capability.delete", "capability", capability["key"], details={"kind": capability["kind"]})


@router.post("/{capability_id}/discover")
async def discover_endpoint(capability_id: uuid.UUID, claims: dict = Depends(require_capabilities_admin)):
    """MCP: reads the server's tool list into the whitelist. New tools start disabled and not read-only;
    a whitelisted tool whose description or schema changed upstream, or that disappeared, is disabled.
    The server's own read-only/destructive hints come back in `hints`, for the admin to judge."""
    capability = capability_or_404(capability_id)
    if capability["kind"] != "mcp":
        raise HTTPException(status_code=400, detail="Solo los servidores MCP tienen descubrimiento.")
    try:
        report = await mcp_runtime.discover(capability, by(claims))
    except CapabilityError as e:
        raise HTTPException(status_code=502, detail=e.message) from None
    audit.record(claims, "capability.discover", "capability", capability["key"],
                 details={k: v for k, v in report.items() if k != "hints"})
    return report


# --- Operations (the whitelist) ---------------------------------------------------------------------------


@router.get("/{capability_id}/operations")
def list_operations_endpoint(capability_id: uuid.UUID, _claims: dict = Depends(require_capabilities_admin)):
    capability_or_404(capability_id)
    return {"operations": registry.list_operations(capability_id)}


@router.post("/{capability_id}/operations", status_code=201)
def add_operation_endpoint(
    capability_id: uuid.UUID, request: OperationCreate,
    claims: dict = Depends(require_capabilities_admin), actor: Access = Depends(get_access),
):
    """CLI only: its operations (subcommands) are defined by hand, each with its command template (see
    capabilities/configs.py validate_command). Added disabled."""
    capability = capability_or_404(capability_id)
    if capability["kind"] != "cli":
        raise HTTPException(status_code=400, detail="Solo las capacidades CLI definen operaciones a mano.")
    if request.command is None:
        raise HTTPException(status_code=422, detail="command es obligatorio para una operación CLI.")
    if request.read_only and capability["risk"] != "read" and not actor.is_super_admin:
        raise HTTPException(status_code=403, detail=ONLY_SUPER_ADMIN)
    try:
        operation = registry.add_operation(capability_id, request.operation, request.description, request.arg_schema,
                                           request.limits, request.read_only, by(claims), request.command)
    except ValueError as e:
        invalid(e)
    except APIError as e:
        db_errors(e)
    audit.record(claims, "operation.create", "capability", capability["key"],
                 details={"operation": request.operation, "read_only": request.read_only, "command": request.command})
    return operation


@router.patch("/{capability_id}/operations/{operation}")
def update_operation_endpoint(
    capability_id: uuid.UUID, operation: str, request: OperationUpdate,
    claims: dict = Depends(require_capabilities_admin), actor: Access = Depends(get_access),
):
    capability = capability_or_404(capability_id)
    current = registry.get_operation(capability_id, operation)
    if current is None:
        raise HTTPException(status_code=404, detail="Operación no encontrada.")
    changes = request.model_dump(exclude_none=True)
    if not changes:
        return current
    kind = capability["kind"]
    editable = {"enabled", "limits"}
    if kind in ("mcp", "cli"):
        editable.add("read_only")
    if kind == "cli":
        editable |= {"description", "arg_schema", "command"}
    refused = set(changes) - editable
    if refused:
        detail = FROM_CODE if kind == "tool" else f"No se puede cambiar: {', '.join(sorted(refused))}"
        raise HTTPException(status_code=400, detail=detail)
    if changes.get("read_only") and not current["read_only"] and capability["risk"] != "read" \
            and not actor.is_super_admin:
        raise HTTPException(status_code=403, detail=ONLY_SUPER_ADMIN)
    try:
        updated = registry.update_operation(capability_id, operation, changes, by(claims))
    except ValueError as e:
        invalid(e)
    audit.record(claims, "operation.update", "capability", capability["key"],
                 details={"operation": operation, "changes": changes,
                          "before": {k: current.get(k) for k in changes}})
    return updated


@router.delete("/{capability_id}/operations/{operation}", status_code=204)
def delete_operation_endpoint(
    capability_id: uuid.UUID, operation: str, claims: dict = Depends(require_capabilities_admin)
):
    capability = capability_or_404(capability_id)
    if capability["kind"] != "cli":
        raise HTTPException(status_code=400, detail="Solo se eliminan operaciones de capacidades CLI.")
    if not registry.delete_operation(capability_id, operation):
        raise HTTPException(status_code=404, detail="Operación no encontrada.")
    audit.record(claims, "operation.delete", "capability", capability["key"], details={"operation": operation})


# --- Connections ------------------------------------------------------------------------------------------


@router.get("/{capability_id}/connections")
def list_connections_endpoint(capability_id: uuid.UUID, _claims: dict = Depends(require_capabilities_admin)):
    capability_or_404(capability_id)
    return {"connections": registry.list_connections(capability_id)}


@router.post("/{capability_id}/connections", status_code=201)
def create_connection_endpoint(
    capability_id: uuid.UUID, request: ConnectionCreate, claims: dict = Depends(require_capabilities_admin)
):
    """The secret is set afterwards with PUT /connections/{id}/secret."""
    capability = capability_or_404(capability_id)
    try:
        connection = registry.create_connection(capability_id, request.name, request.auth_type, request.config,
                                                request.scopes, claims.get("user_id"), by(claims))
    except ValueError as e:
        invalid(e)
    except APIError as e:
        db_errors(e)
    audit.record(claims, "connection.create", "connection", connection["id"],
                 details={"capability": capability["key"], "name": request.name, "auth_type": request.auth_type})
    return connection


@router.patch("/connections/{connection_id}")
def update_connection_endpoint(
    connection_id: uuid.UUID, request: ConnectionUpdate, claims: dict = Depends(require_capabilities_admin)
):
    current = connection_or_404(connection_id)
    changes = request.model_dump(exclude_none=True)
    if not changes:
        return current
    try:
        connection = registry.update_connection(connection_id, changes, by(claims))
    except ValueError as e:
        invalid(e)
    except APIError as e:
        db_errors(e)
    audit.record(claims, "connection.update", "connection", connection_id, details={"changes": changes})
    return connection


@router.put("/connections/{connection_id}/secret")
def set_secret_endpoint(
    connection_id: uuid.UUID, request: SecretSet, claims: dict = Depends(require_capabilities_admin)
):
    """Stores the secret encrypted. The response says only that there is one."""
    connection = connection_or_404(connection_id)
    try:
        updated = registry.set_secret(connection, request.secret, by(claims))
    except secrets.SecretsUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))
    except ValueError as e:
        invalid(e)
    audit.record(claims, "connection.secret_set", "connection", connection_id,
                 details={"fields": sorted(request.secret)})  # the field names, never the values
    return updated


@router.delete("/connections/{connection_id}/secret")
def clear_secret_endpoint(connection_id: uuid.UUID, claims: dict = Depends(require_capabilities_admin)):
    connection_or_404(connection_id)
    updated = registry.clear_secret(connection_id, by(claims))
    audit.record(claims, "connection.secret_clear", "connection", connection_id)
    return updated


@router.delete("/connections/{connection_id}", status_code=204)
def delete_connection_endpoint(connection_id: uuid.UUID, claims: dict = Depends(require_capabilities_admin)):
    connection = connection_or_404(connection_id)
    registry.delete_connection(connection_id)
    audit.record(claims, "connection.delete", "connection", connection_id, details={"name": connection["name"]})


@router.post("/connections/{connection_id}/oauth/start")
async def oauth_start_endpoint(
    connection_id: uuid.UUID, request: OAuthStart, http_request: Request,
    claims: dict = Depends(require_capabilities_admin),
):
    """Starts the OAuth consent of an MCP server's connection (auth_type "oauth"). Open the returned
    authorization_url in a popup; when the admin consents, the provider redirects to
    /admin/capabilities/oauth/callback, which stores the tokens and posts {"type": "mcp-oauth-callback",
    "connection_id", "status", "error"} to the window that called this (its Origin). connected=true:
    the server needed no consent."""
    connection = connection_or_404(connection_id)
    if connection["auth_type"] != "oauth":
        raise HTTPException(status_code=400, detail="La conexión no es de tipo OAuth.")
    capability = capability_or_404(connection["capability_id"])
    transport_kind = (capability.get("config") or {}).get("transport", "streamable_http")
    if capability["kind"] != "mcp" or transport_kind != "streamable_http":
        raise HTTPException(status_code=400, detail="OAuth solo se usa con servidores MCP por HTTP.")
    if not secrets.configured():
        raise HTTPException(status_code=503, detail="Falta CAPABILITY_SECRET_KEY: no se pueden guardar los tokens.")
    origin = http_request.headers.get("origin")
    opener = normalize_origin(origin) if origin and origin_allowed(origin) else None
    try:
        url = await oauth.start(Target(registry._capability(capability), connection), opener, claims,
                                request.client_id, request.client_secret)
    except CapabilityError as e:
        raise HTTPException(status_code=502, detail=e.message) from None
    audit.record(claims, "connection.oauth_start", "connection", connection_id,
                 details={"capability": capability["key"], "client": "manual" if request.client_id else "registered"})
    return {"authorization_url": url, "connected": url is None}


@router.post("/connections/{connection_id}/check")
async def check_connection_endpoint(connection_id: uuid.UUID, claims: dict = Depends(require_capabilities_admin)):
    connection = connection_or_404(connection_id)
    result = await health.check_connection(connection)
    audit.record(claims, "connection.check", "connection", connection_id,
                 outcome="ok" if result and result["status"] != "error" else "error",
                 details={"status": result and result["status"]})
    return result
