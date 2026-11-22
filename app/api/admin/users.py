"""Admin endpoints for users. Users are only created by their own Microsoft 365 login, so there is
no create endpoint: admins list them and approve, hold or disable them."""

import asyncio
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from api.deps import get_access, require
from models.users import UserStatusRequest
from rbac.model import Access
from rbac import audit
from rbac import service as rbac
from rbac.catalog import SUPER_ADMIN
from users import service as users

router = APIRouter(prefix="/admin/users")

require_users_admin = require("admin", "admin_area", "users")


@router.get("")
async def list_users_endpoint(
    status: Literal["active", "pending", "disabled"] | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _claims: dict = Depends(require_users_admin),
):
    """Registered users, most recent login first."""
    return {"users": await asyncio.to_thread(users.list_users, status, limit, offset)}


@router.post("/{user_id}/status")
async def set_user_status_endpoint(
    user_id: uuid.UUID,
    request: UserStatusRequest,
    claims: dict = Depends(require_users_admin),
    actor: Access = Depends(get_access),
):
    """Approve (active), hold (pending) or disable a user. Takes effect at once on this server and
    within RBAC_CACHE_SECONDS on other replicas; a disabled user's token can't be refreshed."""
    if str(user_id) == claims.get("user_id"):
        raise HTTPException(status_code=400, detail="No puedes cambiar el estado de tu propia cuenta.")
    if not actor.is_super_admin and SUPER_ADMIN in await asyncio.to_thread(roles_held_by_hand, user_id):
        raise HTTPException(status_code=403, detail="Solo un super administrador puede cambiar el estado de otro.")
    user = await asyncio.to_thread(
        users.set_status, user_id, request.status, claims.get("sub") or claims.get("user_id") or "?"
    )
    if user is None:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")
    rbac.invalidate(user.id)
    await asyncio.to_thread(audit.record, claims, "user.status", "user", user.id, details={"status": user.status})
    return {"id": user.id, "status": user.status}


def roles_held_by_hand(user_id) -> set[str]:
    keys = {r["id"]: r["key"] for r in rbac.list_roles()}
    return {keys.get(g["role_id"]) for g in rbac.list_user_roles(user_id)}
