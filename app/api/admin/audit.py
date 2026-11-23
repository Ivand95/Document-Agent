"""Audit log viewer (needs `admin` on the `audit` admin area)."""
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query

from api.deps import require
from rbac import audit

router = APIRouter(prefix="/admin/audit")


@router.get("")
def list_audit_endpoint(
    user_id: uuid.UUID | None = None,
    action: str | None = Query(None, max_length=100),
    resource_type: str | None = Query(None, max_length=50),
    resource_id: str | None = Query(None, max_length=200),
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(100, ge=1, le=audit.MAX_LIMIT),
    offset: int = Query(0, ge=0),
    _claims: dict = Depends(require("admin", "admin_area", "audit")),
):
    """Newest first."""
    return {
        "entries": audit.list_entries(user_id, action, resource_type, resource_id, since, until, limit, offset)
    }
