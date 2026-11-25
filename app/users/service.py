"""Users are registered just in time by a successful Microsoft 365 login of our tenant.

There is deliberately no way to create a user from the API: a record always comes from a real login.
Admins can only change the status of users that already exist.
"""
import logging
import os
import re
import uuid
from dataclasses import dataclass

from core.config import SUPABASE_SCHEMA, _plain, global_supabase_client

log = logging.getLogger("users")

STATUSES = ("active", "pending", "disabled")
USERS_TABLE = "users"
LIST_FIELDS = "id,email,name,department,position,status,first_login_at,last_login_at,updated_at,updated_by"
DETAIL_FIELDS = LIST_FIELDS + ",perm_version,created_by"
ADMIN_DEPARTMENTS_TABLE = "user_admin_departments"
# What a search may contain: letters, digits, spaces and @ . - ' (the rest would be PostgREST or LIKE syntax)
_NOT_SEARCHABLE = re.compile(r"[^\w@.\-' ]|_")


@dataclass(frozen=True)
class User:
    id: str
    status: str
    perm_version: int
    created: bool = False

    @property
    def active(self) -> bool:
        return self.status == "active"


def default_status() -> str:
    """Status of a user on their first login: USER_DEFAULT_STATUS, `active` (default) or `pending`.
    Any other value fails closed to `pending`."""
    value = os.getenv("USER_DEFAULT_STATUS", "active").strip().lower()
    if value not in ("active", "pending"):
        log.error("USER_DEFAULT_STATUS must be 'active' or 'pending' (got %r); new users will be pending", value)
        return "pending"
    return value


def _db():
    return global_supabase_client.schema(SUPABASE_SCHEMA)


def _valid_id(user_id) -> bool:
    try:
        uuid.UUID(str(user_id))
        return True
    except ValueError:
        return False


def provision(profile) -> User:
    """Creates the user on their first login or refreshes their profile (department, position, name,
    email) on later ones. Returns the stored status, which the caller must check before issuing a token."""
    if not profile.id:
        raise ValueError("The Microsoft profile has no object id")
    rows = _db().rpc(
        "upsert_m365_user",
        {
            "p_oid": profile.id,
            "p_email": profile.email,
            "p_name": profile.name,
            "p_department": profile.department,
            "p_position": profile.position,
            "p_default_status": default_status(),
        },
    ).execute().data
    row = rows[0]
    user = User(
        id=row["user_id"], status=row["user_status"], perm_version=row["user_perm_version"], created=row["created"]
    )
    if user.created:
        # No roles to assign: the ones that follow from department/position (role_mappings) are worked
        # out at every check, so they always match the current Microsoft 365 profile
        log.info("New user registered from Microsoft 365 login (user %s, status %s)", user.id, user.status)
    return user


def get_user(user_id) -> User | None:
    if not _valid_id(user_id):
        return None
    rows = _db().table(USERS_TABLE).select("id,status,perm_version").eq("id", str(user_id)).execute().data
    if not rows:
        return None
    return User(id=rows[0]["id"], status=rows[0]["status"], perm_version=rows[0]["perm_version"])


def search_text(q: str | None) -> str | None:
    """The search as it is sent to the database: without characters that would be query syntax."""
    cleaned = " ".join(_NOT_SEARCHABLE.sub(" ", q or "").split())[:100]
    return cleaned or None


def _filtered(fields: str, q: str | None, departments: list[str] | None, status: str | None = None, **select):
    query = _db().table(USERS_TABLE).select(fields, **select)
    text = search_text(q)
    if text:
        query = query.or_(f'name.ilike."*{text}*",email.ilike."*{text}*"')
    if departments is not None:
        query = query.in_("department", departments)
    if status:
        query = query.eq("status", status)
    return query


def search_users(status: str | None = None, q: str | None = None, departments: list[str] | None = None,
                 limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
    """A page of users (most recent login first) and how many match in all. `q` searches name and email;
    `departments` lists the exact spellings to keep (None: any department, [] : none)."""
    if departments == []:
        return [], 0
    result = (_filtered(LIST_FIELDS, q, departments, status, count="exact")
              .order("last_login_at", desc=True).range(offset, offset + limit - 1).execute())
    return result.data, result.count or 0


def count_by_status(q: str | None = None, departments: list[str] | None = None) -> dict:
    """{"pending": n, "active": n, "disabled": n, "all": n} for the same search and departments."""
    counts = {status: 0 for status in ("pending", "active", "disabled")}
    if departments != []:
        for status in counts:
            counts[status] = _filtered("id", q, departments, status, count="exact").limit(1).execute().count or 0
    counts["all"] = sum(counts.values())
    return counts


def list_users(status: str | None = None, limit: int = 100, offset: int = 0) -> list[dict]:
    """Most recent logins first."""
    return search_users(status, None, None, limit, offset)[0]


def get_profile(user_id) -> dict | None:
    if not _valid_id(user_id):
        return None
    rows = _db().table(USERS_TABLE).select(DETAIL_FIELDS).eq("id", str(user_id)).execute().data
    return rows[0] if rows else None


def department_spellings() -> list[str]:
    """Every department as written in the users' Microsoft 365 profiles (one org: a small column)."""
    rows = _db().table(USERS_TABLE).select("department").execute().data
    return sorted({r["department"] for r in rows if (r.get("department") or "").strip()}, key=_plain)


def known_departments() -> list[str]:
    """One spelling per department (compared without accents or case): the users' own spelling first,
    then the department mappings of the roles (departments nobody from has signed in yet)."""
    found: dict = {}
    for department in department_spellings():
        found.setdefault(_plain(department), department)
    for row in _db().table("role_mappings").select("value").eq("kind", "department").execute().data:
        if (row.get("value") or "").strip():
            found.setdefault(_plain(row["value"]), row["value"].strip())
    return sorted(found.values(), key=_plain)


# --- Extra departments of a department admin ---------------------------------------------------------------


def list_admin_departments(user_id) -> list[dict]:
    if not _valid_id(user_id):
        return []
    return (
        _db().table(ADMIN_DEPARTMENTS_TABLE).select("department,created_at,created_by")
        .eq("user_id", str(user_id)).order("department").execute().data
    )


def add_admin_department(user_id, department: str, by: str) -> dict:
    """Raises APIError 23505 if it is already there."""
    return _db().table(ADMIN_DEPARTMENTS_TABLE).insert(
        {"user_id": str(user_id), "department": department, "created_by": by}
    ).execute().data[0]


def remove_admin_department(user_id, department: str) -> bool:
    rows = (
        _db().table(ADMIN_DEPARTMENTS_TABLE).delete().eq("user_id", str(user_id)).eq("department", department)
        .execute().data
    )
    return bool(rows)


def set_status(user_id, status: str, by: str) -> User | None:
    """Approve (active), hold (pending) or disable a user. Returns None if the user doesn't exist."""
    if status not in STATUSES:
        raise ValueError(f"Unknown status '{status}'")
    if not _valid_id(user_id):
        return None
    rows = _db().rpc("set_user_status", {"p_user_id": str(user_id), "p_status": status, "p_by": by}).execute().data
    if not rows:
        return None
    row = rows[0]
    log.info("User %s set to %s", row["user_id"], row["user_status"])
    return User(id=row["user_id"], status=row["user_status"], perm_version=row["user_perm_version"])
