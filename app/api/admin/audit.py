"""Audit log viewer (needs `admin` on the `audit` admin area)."""
import re
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query

from api.deps import require
from rbac import audit

router = APIRouter(prefix="/admin/audit")

ACTION_PREFIX = re.compile(r"[a-z][a-z_]*\.?")
MAX_ACTION_PREFIXES = 10


def _action_prefixes(raw: str | None) -> list[str] | None:
    """"user.,role." → ["user.", "role."]. Only lowercase letters, "_" and a final ".", so nothing can break
    out of the PostgREST filter."""
    if raw is None:
        return None
    prefixes = [prefix.strip() for prefix in raw.split(",") if prefix.strip()]
    if (
        not prefixes
        or len(prefixes) > MAX_ACTION_PREFIXES
        or not all(ACTION_PREFIX.fullmatch(prefix) for prefix in prefixes)
    ):
        raise HTTPException(status_code=422, detail="action_prefix no válido.")
    return prefixes


@router.get("")
def list_audit_endpoint(
    user_id: uuid.UUID | None = None,
    action: str | None = Query(None, max_length=100),
    action_prefix: str | None = Query(None, max_length=300),
    resource_type: str | None = Query(None, max_length=50),
    resource_id: str | None = Query(None, max_length=200),
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(100, ge=1, le=audit.MAX_LIMIT),
    offset: int = Query(0, ge=0),
    _claims: dict = Depends(require("admin", "admin_area", "audit")),
):
    """Newest first. action_prefix: one or more comma-separated prefixes ("role.,mapping.")."""
    return {
        "entries": audit.list_entries(
            user_id=user_id, action=action, resource_type=resource_type, resource_id=resource_id,
            since=since, until=until, limit=limit, offset=offset,
            action_prefixes=_action_prefixes(action_prefix),
        )
    }
