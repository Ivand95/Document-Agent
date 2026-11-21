"""Users are registered just in time by a successful Microsoft 365 login of our tenant.

There is deliberately no way to create a user from the API: a record always comes from a real login.
Admins can only change the status of users that already exist.
"""
import logging
import os
import uuid
from dataclasses import dataclass

from core.config import SUPABASE_SCHEMA, global_supabase_client

log = logging.getLogger("users")

STATUSES = ("active", "pending", "disabled")
USERS_TABLE = "users"
LIST_FIELDS = "id,email,name,department,position,status,first_login_at,last_login_at,updated_at,updated_by"


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
        # Default roles from department/position mappings are assigned here once RBAC exists (WP3)
        log.info("New user registered from Microsoft 365 login (user %s, status %s)", user.id, user.status)
    return user


def get_user(user_id) -> User | None:
    if not _valid_id(user_id):
        return None
    rows = _db().table(USERS_TABLE).select("id,status,perm_version").eq("id", str(user_id)).execute().data
    if not rows:
        return None
    return User(id=rows[0]["id"], status=rows[0]["status"], perm_version=rows[0]["perm_version"])


def list_users(status: str | None = None, limit: int = 100, offset: int = 0) -> list[dict]:
    """Most recent logins first."""
    query = _db().table(USERS_TABLE).select(LIST_FIELDS)
    if status:
        query = query.eq("status", status)
    return query.order("last_login_at", desc=True).range(offset, offset + limit - 1).execute().data


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
