"""Loads RBAC data from Supabase, caches it briefly, and changes it for the admin API.

Every check reads a cached copy: the shared policy (roles, mappings, role permissions) and each user's
own data (status, department, position, roles given by hand, direct permissions) are reloaded after
RBAC_CACHE_SECONDS (default 30). Changes made through this process clear the cache at once; changes
made by another replica or directly in the database are seen within that time. That also bounds how
long a disabled user keeps working with a token that hasn't expired yet.
"""
import logging
import os
import threading
import time
import uuid
from datetime import datetime

from core.config import SUPABASE_SCHEMA, global_supabase_client
from rbac import legacy
from rbac.catalog import EFFECTS, MAPPING_KINDS, SUBJECT_TYPES, SUPER_ADMIN, validate_resource
from rbac.model import Access, Grant, Mapping, Policy, Role, Rule, Subject

log = logging.getLogger("rbac")

CACHE_SECONDS = float(os.getenv("RBAC_CACHE_SECONDS", "30"))
MAX_CACHED_USERS = 5000
RULE_FIELDS = "id,subject_type,subject_id,resource_type,resource_id,action,effect,constraints"

_lock = threading.Lock()
_policy: tuple[float, Policy] | None = None
_subjects: dict = {}  # user id -> (loaded at, Subject | None)


def _db():
    return global_supabase_client.schema(SUPABASE_SCHEMA)


def valid_uuid(value) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, TypeError):
        return False


def _time(value):
    if not value:
        return None
    return value if isinstance(value, datetime) else datetime.fromisoformat(str(value))


def _role(row) -> Role:
    return Role(
        id=row["id"], key=row["key"], name=row["name"], description=row.get("description"),
        is_system=bool(row.get("is_system")),
    )


def _rule(row) -> Rule:
    return Rule(
        id=row["id"], subject_type=row["subject_type"], subject_id=row["subject_id"],
        resource_type=row["resource_type"], resource_id=row["resource_id"], action=row["action"],
        effect=row["effect"], constraints=row.get("constraints") or {},
    )


# --- Loading --------------------------------------------------------------------------------------


def load_policy() -> Policy:
    db = _db()
    roles = {r["id"]: _role(r) for r in db.table("roles").select("id,key,name,description,is_system").execute().data}
    mappings = tuple(
        Mapping(id=m["id"], kind=m["kind"], value=m["value"], role_id=m["role_id"])
        for m in db.table("role_mappings").select("id,kind,value,role_id").execute().data
    )
    role_rules: dict = {}
    for row in db.table("permissions").select(RULE_FIELDS).eq("subject_type", "role").execute().data:
        role_rules.setdefault(row["subject_id"], []).append(_rule(row))
    return Policy(roles=roles, mappings=mappings, role_rules={k: tuple(v) for k, v in role_rules.items()})


def load_subject(user_id: str) -> Subject | None:
    db = _db()
    rows = (
        db.table("users").select("id,status,perm_version,department,position").eq("id", user_id).execute().data
    )
    if not rows:
        return None
    user = rows[0]
    grants = tuple(
        Grant(role_id=g["role_id"], expires_at=_time(g.get("expires_at")), granted_by=g.get("granted_by"))
        for g in db.table("user_roles").select("role_id,expires_at,granted_by").eq("user_id", user_id).execute().data
    )
    rules = tuple(
        _rule(r)
        for r in db.table("permissions").select(RULE_FIELDS)
        .eq("subject_type", "user").eq("subject_id", user_id).execute().data
    )
    return Subject(
        user_id=user["id"], status=user["status"], perm_version=user["perm_version"],
        department=user.get("department"), position=user.get("position"), grants=grants, rules=rules,
    )


def policy() -> Policy:
    global _policy
    now = time.monotonic()
    with _lock:
        cached = _policy
    if cached and now - cached[0] < CACHE_SECONDS:
        return cached[1]
    fresh = load_policy()
    with _lock:
        _policy = (now, fresh)
    return fresh


def subject(user_id: str) -> Subject | None:
    now = time.monotonic()
    with _lock:
        cached = _subjects.get(user_id)
    if cached and now - cached[0] < CACHE_SECONDS:
        return cached[1]
    fresh = load_subject(user_id)
    with _lock:
        if len(_subjects) >= MAX_CACHED_USERS:
            for key in sorted(_subjects, key=lambda k: _subjects[k][0])[: MAX_CACHED_USERS // 10]:
                del _subjects[key]
        _subjects[user_id] = (now, fresh)
    return fresh


def access_for(user_id) -> Access | None:
    """What the user may do now, or None if there is no such user. Raises on database errors:
    callers must fail closed."""
    if not valid_uuid(user_id):
        return None
    user = subject(str(user_id))
    if user is None:
        return None
    return Access(user, policy(), legacy.legacy_role_keys(user.department))


def invalidate(user_id=None):
    """Forget cached data: one user's, or everything (after a change to roles, mappings or role permissions)."""
    global _policy
    with _lock:
        if user_id is None:
            _policy = None
            _subjects.clear()
        else:
            _subjects.pop(str(user_id), None)


# --- Reading for the admin API ----------------------------------------------------------------------


def list_roles() -> list[dict]:
    return _db().table("roles").select("*").order("key").execute().data


def get_role(role_id) -> dict | None:
    if not valid_uuid(role_id):
        return None
    rows = _db().table("roles").select("*").eq("id", str(role_id)).execute().data
    return rows[0] if rows else None


def list_mappings() -> list[dict]:
    return _db().table("role_mappings").select("*").order("kind").order("value").execute().data


def get_mapping(mapping_id) -> dict | None:
    if not valid_uuid(mapping_id):
        return None
    rows = _db().table("role_mappings").select("*").eq("id", str(mapping_id)).execute().data
    return rows[0] if rows else None


def list_permissions(subject_type=None, subject_id=None, resource_type=None) -> list[dict]:
    query = _db().table("permissions").select("*")
    if subject_type:
        query = query.eq("subject_type", subject_type)
    if subject_id:
        query = query.eq("subject_id", str(subject_id))
    if resource_type:
        query = query.eq("resource_type", resource_type)
    return query.order("resource_type").order("resource_id").order("action").execute().data


def get_permission(permission_id) -> dict | None:
    if not valid_uuid(permission_id):
        return None
    rows = _db().table("permissions").select("*").eq("id", str(permission_id)).execute().data
    return rows[0] if rows else None


def list_user_roles(user_id) -> list[dict]:
    return (
        _db().table("user_roles").select("role_id,granted_by,granted_at,expires_at")
        .eq("user_id", str(user_id)).execute().data
    )


def role_holders(role_id) -> list[str]:
    """Users given this role by hand (not through a mapping)."""
    rows = _db().table("user_roles").select("user_id,expires_at").eq("role_id", str(role_id)).execute().data
    return [r["user_id"] for r in rows]


def active_super_admins() -> list[str]:
    """Users who hold super_admin by hand, are active, and whose grant hasn't expired."""
    role = next((r for r in list_roles() if r["key"] == SUPER_ADMIN), None)
    if role is None:
        return []
    now = datetime.now().astimezone()
    holders = []
    for row in _db().table("user_roles").select("user_id,expires_at").eq("role_id", role["id"]).execute().data:
        expires = _time(row.get("expires_at"))
        if expires is None or expires > now:
            holders.append(row["user_id"])
    if not holders:
        return []
    users = _db().table("users").select("id,status").in_("id", holders).execute().data
    return [u["id"] for u in users if u["status"] == "active"]


def user_exists(user_id) -> bool:
    if not valid_uuid(user_id):
        return False
    return bool(_db().table("users").select("id").eq("id", str(user_id)).execute().data)


def find_user_by_email(email: str) -> dict | None:
    rows = (
        _db().table("users").select("id,email,name,status").eq("email", (email or "").strip().lower())
        .execute().data
    )
    return rows[0] if rows else None


# --- Changing (the API checks who may do it; these only validate the data) ---------------------------


def create_role(key: str, name: str, description: str | None, by: str) -> dict:
    row = _db().table("roles").insert(
        {"key": key, "name": name, "description": description, "is_system": False, "created_by": by}
    ).execute().data[0]
    invalidate()
    return row


def update_role(role_id, name: str | None, description: str | None, by: str) -> dict | None:
    changes = {"updated_at": datetime.now().astimezone().isoformat(), "updated_by": by}
    if name is not None:
        changes["name"] = name
    if description is not None:
        changes["description"] = description
    rows = _db().table("roles").update(changes).eq("id", str(role_id)).execute().data
    invalidate()
    return rows[0] if rows else None


def delete_role(role_id) -> bool:
    """System roles can't be deleted (the caller checks first; this only deletes non-system ones)."""
    rows = _db().table("roles").delete().eq("id", str(role_id)).eq("is_system", False).execute().data
    invalidate()
    return bool(rows)


def assign_role(user_id, role_id, by: str, expires_at: datetime | None = None) -> dict:
    row = _db().table("user_roles").upsert(
        {
            "user_id": str(user_id),
            "role_id": str(role_id),
            "granted_by": by,
            "granted_at": datetime.now().astimezone().isoformat(),
            "expires_at": expires_at.isoformat() if expires_at else None,
        },
        on_conflict="user_id,role_id",
    ).execute().data[0]
    invalidate(user_id)
    return row


def revoke_role(user_id, role_id) -> bool:
    rows = _db().table("user_roles").delete().eq("user_id", str(user_id)).eq("role_id", str(role_id)).execute().data
    invalidate(user_id)
    return bool(rows)


def add_mapping(kind: str, value: str, role_id, by: str) -> dict:
    if kind not in MAPPING_KINDS:
        raise ValueError(f"Tipo de asignación desconocido: {kind}")
    value = "" if kind == "everyone" else (value or "")
    if kind == "department":
        value = value.strip()
    if kind != "everyone" and not value.strip():
        raise ValueError("El valor no puede estar vacío")
    row = _db().table("role_mappings").insert(
        {"kind": kind, "value": value, "role_id": str(role_id), "created_by": by}
    ).execute().data[0]
    invalidate()
    return row


def delete_mapping(mapping_id) -> bool:
    rows = _db().table("role_mappings").delete().eq("id", str(mapping_id)).execute().data
    invalidate()
    return bool(rows)


def set_permission(
    subject_type: str, subject_id, resource_type: str, resource_id: str, action: str, effect: str,
    constraints: dict | None, by: str,
) -> dict:
    """Creates the permission or, if one exists for the same subject, resource and action, replaces
    its effect and constraints."""
    if subject_type not in SUBJECT_TYPES:
        raise ValueError(f"Tipo de sujeto desconocido: {subject_type}")
    if effect not in EFFECTS:
        raise ValueError(f"Efecto desconocido: {effect}")
    validate_resource(resource_type, resource_id)
    now = datetime.now().astimezone().isoformat()
    row = _db().table("permissions").upsert(
        {
            "subject_type": subject_type,
            "subject_id": str(subject_id),
            "resource_type": resource_type,
            "resource_id": resource_id,
            "action": action,
            "effect": effect,
            "constraints": constraints or {},
            "created_by": by,
            "updated_at": now,
            "updated_by": by,
        },
        on_conflict="subject_type,subject_id,resource_type,resource_id,action",
    ).execute().data[0]
    invalidate(subject_id if subject_type == "user" else None)
    return row


def delete_permission(permission_id) -> dict | None:
    rows = _db().table("permissions").delete().eq("id", str(permission_id)).execute().data
    if not rows:
        return None
    row = rows[0]
    invalidate(row["subject_id"] if row["subject_type"] == "user" else None)
    return row
