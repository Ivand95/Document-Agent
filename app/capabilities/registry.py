"""Reads and changes the capability registry in Supabase.

The gateway reads a cached snapshot (capabilities and their operations), reloaded after
CAPABILITY_CACHE_SECONDS (default 30) and cleared at once by changes made in this process.
Connection secrets are never part of the snapshot: they are read and decrypted only when a call needs them.
"""
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime

from capabilities import native, secrets
from capabilities.catalog import AUTH_TYPES, CONNECTION_STATUSES, DATA_CLASSES, KINDS, RISKS
from capabilities.configs import (
    validate_arg_schema, validate_config, validate_connection_config, validate_limits, validate_secret,
)
from core.config import SUPABASE_SCHEMA, global_supabase_client
from rbac import catalog as rbac_catalog
from rbac.service import valid_uuid

log = logging.getLogger("capabilities")

CACHE_SECONDS = float(os.getenv("CAPABILITY_CACHE_SECONDS", "30"))
CAPABILITY_FIELDS = "id,key,kind,name,description,enabled,risk,data_class,config,version"
OPERATION_FIELDS = "capability_id,operation,description,enabled,read_only,arg_schema,limits"
# Everything but the ciphertext: the API never sees it
CONNECTION_FIELDS = (
    "id,capability_id,name,auth_type,config,scopes,secret_updated_at,status,status_detail,last_check_at,"
    "owner_user_id,created_at,created_by,updated_at,updated_by"
)


@dataclass(frozen=True)
class Operation:
    capability_id: str
    operation: str
    description: str = ""
    enabled: bool = False
    read_only: bool = False
    arg_schema: dict = field(default_factory=lambda: {"type": "object"})
    limits: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Capability:
    id: str
    key: str
    kind: str
    name: str
    description: str = ""
    enabled: bool = False
    risk: str = "read"
    data_class: str = "sensitive"
    config: dict = field(default_factory=dict)
    version: int = 1
    operations: tuple[Operation, ...] = ()

    def operation(self, name: str) -> Operation | None:
        return next((o for o in self.operations if o.operation == name), None)


@dataclass(frozen=True)
class Snapshot:
    capabilities: dict[str, Capability]  # by key

    def get(self, key: str) -> Capability | None:
        return self.capabilities.get(key)


def _db():
    return global_supabase_client.schema(SUPABASE_SCHEMA)


def _now() -> str:
    return datetime.now().astimezone().isoformat()


def _operation(row) -> Operation:
    return Operation(
        capability_id=row["capability_id"], operation=row["operation"], description=row.get("description") or "",
        enabled=bool(row.get("enabled")), read_only=bool(row.get("read_only")),
        arg_schema=row.get("arg_schema") or {"type": "object"}, limits=row.get("limits") or {},
    )


def _capability(row, operations=()) -> Capability:
    return Capability(
        id=row["id"], key=row["key"], kind=row["kind"], name=row["name"], description=row.get("description") or "",
        enabled=bool(row.get("enabled")), risk=row.get("risk") or "read",
        data_class=row.get("data_class") or "sensitive", config=row.get("config") or {},
        version=row.get("version") or 1, operations=tuple(operations),
    )


# --- Snapshot for the gateway ------------------------------------------------------------------------

_lock = threading.Lock()
_snapshot: tuple[float, Snapshot] | None = None


def load_snapshot() -> Snapshot:
    db = _db()
    rows = db.table("capabilities").select(CAPABILITY_FIELDS).execute().data
    operations: dict = {}
    for row in db.table("capability_operations").select(OPERATION_FIELDS).execute().data:
        operations.setdefault(row["capability_id"], []).append(_operation(row))
    return Snapshot({
        r["key"]: _capability(r, sorted(operations.get(r["id"], []), key=lambda o: o.operation)) for r in rows
    })


def snapshot() -> Snapshot:
    global _snapshot
    now = time.monotonic()
    with _lock:
        cached = _snapshot
    if cached and now - cached[0] < CACHE_SECONDS:
        return cached[1]
    fresh = load_snapshot()
    with _lock:
        _snapshot = (now, fresh)
    return fresh


def invalidate():
    global _snapshot
    with _lock:
        _snapshot = None


# Permissions on capabilities can only name capabilities that exist
rbac_catalog.register_source("capability", lambda: {k: c.name for k, c in snapshot().capabilities.items()})


# --- Reading for the admin API ------------------------------------------------------------------------


def list_capabilities(kind=None) -> list[dict]:
    query = _db().table("capabilities").select("*")
    if kind:
        query = query.eq("kind", kind)
    return query.order("key").execute().data


def get_capability(capability_id) -> dict | None:
    if not valid_uuid(capability_id):
        return None
    rows = _db().table("capabilities").select("*").eq("id", str(capability_id)).execute().data
    return rows[0] if rows else None


def list_operations(capability_id) -> list[dict]:
    return (
        _db().table("capability_operations").select("*").eq("capability_id", str(capability_id))
        .order("operation").execute().data
    )


def get_operation(capability_id, operation: str) -> dict | None:
    rows = (
        _db().table("capability_operations").select("*").eq("capability_id", str(capability_id))
        .eq("operation", operation).execute().data
    )
    return rows[0] if rows else None


def list_connections(capability_id=None) -> list[dict]:
    query = _db().table("connections").select(CONNECTION_FIELDS)
    if capability_id is not None:
        query = query.eq("capability_id", str(capability_id))
    return [_public_connection(r) for r in query.order("name").execute().data]


def get_connection(connection_id) -> dict | None:
    if not valid_uuid(connection_id):
        return None
    rows = _db().table("connections").select(CONNECTION_FIELDS).eq("id", str(connection_id)).execute().data
    return _public_connection(rows[0]) if rows else None


def _public_connection(row: dict) -> dict:
    row = dict(row)
    row.pop("secret_ciphertext", None)
    row["has_secret"] = row.get("secret_updated_at") is not None
    return row


# --- Changing capabilities (the API checks who may do it; these validate the data) -------------------


def _check_enums(risk=None, data_class=None):
    if risk is not None and risk not in RISKS:
        raise ValueError(f"Riesgo desconocido: {risk}")
    if data_class is not None and data_class not in DATA_CLASSES:
        raise ValueError(f"Clase de datos desconocida: {data_class}")


def create_capability(key, kind, name, description, risk, data_class, config, by: str) -> dict:
    if kind not in KINDS:
        raise ValueError(f"Tipo de capacidad desconocido: {kind}")
    _check_enums(risk, data_class)
    row = _db().table("capabilities").insert({
        "key": key, "kind": kind, "name": name, "description": description or "", "enabled": False,
        "risk": risk, "data_class": data_class, "config": validate_config(kind, config or {}), "created_by": by,
    }).execute().data[0]
    invalidate()
    return row


def update_capability(capability: dict, changes: dict, by: str, new_version: bool = False) -> dict:
    """changes: any of name, description, enabled, risk, data_class, config (and last_test, for the
    tool editors). A config change, or new_version, bumps the version."""
    allowed = {"name", "description", "enabled", "risk", "data_class", "config", "last_test"}
    unknown = set(changes) - allowed
    if unknown:
        raise ValueError(f"No se puede cambiar: {', '.join(sorted(unknown))}")
    _check_enums(changes.get("risk"), changes.get("data_class"))
    update = dict(changes)
    if "config" in update:
        tool = native.get(capability["key"]) if capability["kind"] == "tool" else None
        update["config"] = validate_config(capability["kind"], update["config"], tool.config_schema if tool else None)
        new_version = new_version or update["config"] != capability.get("config")
    if new_version:
        update["version"] = (capability.get("version") or 1) + 1
    update.update({"updated_at": _now(), "updated_by": by})
    rows = _db().table("capabilities").update(update).eq("id", capability["id"]).execute().data
    invalidate()
    return rows[0] if rows else None


def delete_capability(capability_id) -> bool:
    """Also deletes its operations, connections and permissions (database cascade and trigger)."""
    rows = _db().table("capabilities").delete().eq("id", str(capability_id)).execute().data
    invalidate()
    return bool(rows)


def add_operation(capability_id, operation: str, description: str, arg_schema: dict, limits: dict,
                  read_only: bool, by: str) -> dict:
    row = _db().table("capability_operations").insert({
        "capability_id": str(capability_id), "operation": operation, "description": description or "",
        "enabled": False, "read_only": bool(read_only), "arg_schema": validate_arg_schema(arg_schema),
        "limits": validate_limits(limits), "updated_by": by,
    }).execute().data[0]
    invalidate()
    return row


def update_operation(capability_id, operation: str, changes: dict, by: str) -> dict | None:
    """changes: any of enabled, read_only, description, arg_schema, limits (the API decides which ones
    each kind allows)."""
    allowed = {"enabled", "read_only", "description", "arg_schema", "limits"}
    unknown = set(changes) - allowed
    if unknown:
        raise ValueError(f"No se puede cambiar: {', '.join(sorted(unknown))}")
    update = dict(changes)
    if "arg_schema" in update:
        update["arg_schema"] = validate_arg_schema(update["arg_schema"])
    if "limits" in update:
        update["limits"] = validate_limits(update["limits"])
    update.update({"updated_at": _now(), "updated_by": by})
    rows = (
        _db().table("capability_operations").update(update).eq("capability_id", str(capability_id))
        .eq("operation", operation).execute().data
    )
    invalidate()
    return rows[0] if rows else None


def delete_operation(capability_id, operation: str) -> bool:
    rows = (
        _db().table("capability_operations").delete().eq("capability_id", str(capability_id))
        .eq("operation", operation).execute().data
    )
    invalidate()
    return bool(rows)


# --- Connections ----------------------------------------------------------------------------------------


def create_connection(capability_id, name: str, auth_type: str, config: dict, scopes: list[str],
                      owner_user_id, by: str) -> dict:
    if auth_type not in AUTH_TYPES:
        raise ValueError(f"Tipo de autenticación desconocido: {auth_type}")
    row = _db().table("connections").insert({
        "capability_id": None if capability_id is None else str(capability_id), "name": name,
        "auth_type": auth_type, "config": validate_connection_config(config or {}), "scopes": list(scopes or []),
        "owner_user_id": None if owner_user_id is None else str(owner_user_id), "created_by": by,
    }).execute().data[0]
    return _public_connection(row)


def update_connection(connection_id, changes: dict, by: str) -> dict | None:
    """changes: any of name, config, scopes, status ('disabled' to switch it off, 'unchecked' to turn it
    back on)."""
    allowed = {"name", "config", "scopes", "status"}
    unknown = set(changes) - allowed
    if unknown:
        raise ValueError(f"No se puede cambiar: {', '.join(sorted(unknown))}")
    update = dict(changes)
    if "config" in update:
        update["config"] = validate_connection_config(update["config"])
    if "status" in update:
        if update["status"] not in ("disabled", "unchecked"):
            raise ValueError("El estado solo se puede cambiar a 'disabled' o 'unchecked'")
        update["status_detail"] = None
    update.update({"updated_at": _now(), "updated_by": by})
    rows = _db().table("connections").update(update).eq("id", str(connection_id)).execute().data
    return _public_connection(rows[0]) if rows else None


def delete_connection(connection_id) -> bool:
    rows = _db().table("connections").delete().eq("id", str(connection_id)).execute().data
    return bool(rows)


def set_secret(connection: dict, secret: dict, by: str) -> dict | None:
    """Encrypts and stores the secret. The connection must be checked again afterwards."""
    ciphertext = secrets.encrypt(validate_secret(connection["auth_type"], secret))
    now = _now()
    rows = _db().table("connections").update({
        "secret_ciphertext": ciphertext, "secret_updated_at": now,
        "status": "unchecked" if connection.get("status") != "disabled" else "disabled",
        "status_detail": None, "updated_at": now, "updated_by": by,
    }).eq("id", connection["id"]).execute().data
    return _public_connection(rows[0]) if rows else None


def clear_secret(connection_id, by: str) -> dict | None:
    rows = _db().table("connections").update({
        "secret_ciphertext": None, "secret_updated_at": None, "updated_at": _now(), "updated_by": by,
    }).eq("id", str(connection_id)).execute().data
    return _public_connection(rows[0]) if rows else None


def read_secret(connection_id) -> dict | None:
    """The decrypted secret, for executors and health checks only. None if the connection has none.
    Raises SecretsUnavailable / SecretUnreadable."""
    rows = _db().table("connections").select("secret_ciphertext").eq("id", str(connection_id)).execute().data
    if not rows or not rows[0].get("secret_ciphertext"):
        return None
    return secrets.decrypt(rows[0]["secret_ciphertext"])


def record_check(connection_id, status: str, detail: str | None) -> dict | None:
    if status not in CONNECTION_STATUSES:
        raise ValueError(f"Estado desconocido: {status}")
    now = _now()
    rows = _db().table("connections").update({
        "status": status, "status_detail": (detail or None) and detail[:500], "last_check_at": now,
    }).eq("id", str(connection_id)).execute().data
    return _public_connection(rows[0]) if rows else None


def rotate_secrets() -> int:
    """Re-encrypts every stored secret with the first CAPABILITY_SECRET_KEY. Returns how many."""
    rows = _db().table("connections").select("id,secret_ciphertext").not_.is_("secret_ciphertext", "null").execute().data
    for row in rows:
        _db().table("connections").update({"secret_ciphertext": secrets.rotate(row["secret_ciphertext"])}).eq(
            "id", row["id"]
        ).execute()
    return len(rows)


# --- Native tools --------------------------------------------------------------------------------------


def sync_native_tools(by: str = "system") -> dict:
    """Copies the native tools found in code into the registry.

    New tools and new operations are added disabled. For existing ones, the code decides the operations'
    description, argument schema and read-only flag, and operations no longer in the code are removed;
    whether the tool and each operation are enabled, the limits, the data class and the config stay as
    the admin left them. Tools in the registry that are no longer in the code are reported (they can't
    run) but not deleted, so their permissions survive a deploy that drops them by mistake.
    """
    tools = native.native_tools()
    db = _db()
    existing = {r["key"]: r for r in db.table("capabilities").select("id,key,kind").eq("kind", "tool").execute().data}
    report = {"added": [], "updated": [], "missing": sorted(set(existing) - set(tools))}
    for key, tool in tools.items():
        row = existing.get(key)
        if row is None:
            taken = db.table("capabilities").select("id,kind").eq("key", key).execute().data
            if taken:
                log.error("Native tool '%s' clashes with a registered %s of the same key; not synced", key,
                          taken[0]["kind"])
                continue
            row = db.table("capabilities").insert({
                "key": key, "kind": "tool", "name": tool.name, "description": tool.description, "enabled": False,
                "risk": tool.risk, "data_class": tool.data_class, "config": tool.default_config,
                "version": tool.version, "created_by": by,
            }).execute().data[0]
            report["added"].append(key)
        else:
            db.table("capabilities").update({"version": tool.version}).eq("id", row["id"]).execute()
            report["updated"].append(key)
        current = {o["operation"] for o in db.table("capability_operations").select("operation")
                   .eq("capability_id", row["id"]).execute().data}
        for op in tool.operations:
            values = {"description": op.description, "read_only": op.read_only,
                      "arg_schema": validate_arg_schema(op.arg_schema)}
            if op.name in current:
                db.table("capability_operations").update(values).eq("capability_id", row["id"]).eq(
                    "operation", op.name).execute()
            else:
                db.table("capability_operations").insert(
                    {"capability_id": row["id"], "operation": op.name, "enabled": False, "updated_by": by, **values}
                ).execute()
        for gone in current - {o.name for o in tool.operations}:
            db.table("capability_operations").delete().eq("capability_id", row["id"]).eq("operation", gone).execute()
    if report["missing"]:
        log.warning("Native tools registered but not in the code (they can't run): %s", ", ".join(report["missing"]))
    invalidate()
    return report
