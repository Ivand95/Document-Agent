"""Admin endpoints for users. Users are only created by their own Microsoft 365 login, so there is
no create endpoint: admins list them and approve, hold or disable them."""

import asyncio
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from api.deps import require_admin
from models.users import UserStatusRequest
from users import service as users

router = APIRouter(prefix="/admin/users")


@router.get("")
async def list_users_endpoint(
    status: Literal["active", "pending", "disabled"] | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _claims: dict = Depends(require_admin),
):
    """Registered users, most recent login first."""
    return {"users": await asyncio.to_thread(users.list_users, status, limit, offset)}


@router.post("/{user_id}/status")
async def set_user_status_endpoint(
    user_id: uuid.UUID, request: UserStatusRequest, claims: dict = Depends(require_admin)
):
    """Approve (active), hold (pending) or disable a user. Takes effect at their next login or token refresh."""
    if str(user_id) == claims.get("user_id"):
        raise HTTPException(status_code=400, detail="No puedes cambiar el estado de tu propia cuenta.")
    user = await asyncio.to_thread(
        users.set_status, user_id, request.status, claims.get("sub") or claims.get("user_id") or "?"
    )
    if user is None:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")
    return {"id": user.id, "status": user.status}
