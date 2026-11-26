"""Admin endpoints for users. Users are only created by their own Microsoft 365 login, so there is
no create endpoint: admins list them, look at one, and approve, hold or disable them.

Everything needs `admin` on the `users` admin area, and follows the admin's scope (users/admin_scope.py):
a department admin only sees and changes the users of their departments, and never admins or other
department admins. Users outside the scope answer 404, as if they didn't exist. The extra departments of
a department admin are managed with the `rbac` area.

A user's roles (/{id}/roles) are open to the `users` or the `rbac` area. With `rbac`: every user and
every role, with the same rules as /admin/rbac (only a super_admin touches super_admin and super admins,
nobody changes their own roles, the last active super_admin stays). With `users` only: the users in the
scope, not the protected ones, and only roles marked manager_assignable that open no admin area. Roles
that come from a department/position mapping are shown with their origin and can't be removed here.
"""

import asyncio
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from postgrest.exceptions import APIError

from api.deps import get_access, require, require_any
from core.config import _plain
from models.rbac import RoleAssign
from models.users import AdminDepartmentRequest, UserSearchRequest, UserStatusRequest
from rbac import audit
from rbac import service as rbac
from rbac.catalog import SUPER_ADMIN
from rbac.model import Access, delegable
from users import admin_scope
from users import service as users

router = APIRouter(prefix="/admin/users")

require_users_admin = require("admin", "admin_area", "users")
require_rbac_admin = require("admin", "admin_area", "rbac")
require_users_or_rbac_admin = require_any("admin", "admin_area", "users", "rbac")

NOT_FOUND = "Usuario no encontrado."
ONLY_SUPER_ADMIN = "Solo un super administrador puede hacer este cambio."
PROTECTED = {
    "self": "No puedes cambiar el estado de tu propia cuenta.",
    "super_admin": "Solo un super administrador puede cambiar el estado de otro.",
    "admin": "No puedes cambiar el estado de un administrador.",
}


def by(claims: dict) -> str:
    return claims.get("sub") or claims.get("user_id") or "?"


def user_in_scope(user_id, scope: admin_scope.Scope) -> dict:
    """The user's profile, or 404 if there is none or it is outside the admin's scope."""
    profile = users.get_profile(user_id)
    if profile is None or not scope.allows(profile.get("department")):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return profile


async def list_users(
    actor: Access,
    status: str | None,
    q: str | None,
    department: str | None,
    limit: int,
    offset: int,
) -> dict:
    """Users in the admin's scope, most recent login first. `q` searches name and email; `department`
    compares without accents or case. Returns the page, the total, the count per status for the same
    search (for the status filters), the departments the admin can filter by, and, for each user,
    whether the admin can act on them (`actionable`, `actions`, `protected`)."""

    def run():
        scope = admin_scope.scope_of(actor)
        departments = admin_scope.department_filter(scope, department)
        rows, total = users.search_users(status, q, departments, limit, offset)
        return {
            "users": admin_scope.annotate(actor, scope, rows),
            "total": total,
            "counts": users.count_by_status(q, departments),
            "scope": {"limited": scope.limited, "departments": admin_scope.department_options(scope)},
            "limit": limit,
            "offset": offset,
        }

    return await asyncio.to_thread(run)


@router.get("")
async def list_users_endpoint(
    status: Literal["active", "pending", "disabled"] | None = None,
    department: str | None = Query(None, max_length=100),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _claims: dict = Depends(require_users_admin),
    actor: Access = Depends(get_access),
):
    """Users in the admin's scope, without a text search (see list_users). To search by name or email
    use POST /admin/users/search, which keeps the search text out of the URL (and the access logs)."""
    return await list_users(actor, status, None, department, limit, offset)


@router.post("/search")
async def search_users_endpoint(
    request: UserSearchRequest,
    _claims: dict = Depends(require_users_admin),
    actor: Access = Depends(get_access),
):
    """Same answer as GET /admin/users, with the filters (and the `q` search on name and email) in the body."""
    return await list_users(actor, request.status, request.q, request.department, request.limit, request.offset)


@router.get("/{user_id}")
async def get_user_endpoint(
    user_id: uuid.UUID, _claims: dict = Depends(require_users_admin), actor: Access = Depends(get_access)
):
    """One user: profile, first and last login, who made the last change, and what the admin can do."""

    def run():
        scope = admin_scope.scope_of(actor)
        return admin_scope.annotate(actor, scope, [user_in_scope(user_id, scope)])[0]

    return await asyncio.to_thread(run)


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
        raise HTTPException(status_code=400, detail=PROTECTED["self"])

    def check():
        scope = admin_scope.scope_of(actor)
        row = admin_scope.annotate(actor, scope, [user_in_scope(user_id, scope)])[0]
        if row["protected"]:
            raise HTTPException(status_code=403, detail=PROTECTED[row["protected"]])

    await asyncio.to_thread(check)
    user = await asyncio.to_thread(users.set_status, user_id, request.status, by(claims))
    if user is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    rbac.invalidate(user.id)
    await asyncio.to_thread(audit.record, claims, "user.status", "user", user.id, details={"status": user.status})
    return {"id": user.id, "status": user.status}


# --- Departments a department admin manages (rbac area) -------------------------------------------------


def _admin_departments(profile: dict) -> dict:
    return {
        "user_id": profile["id"],
        "is_department_admin": admin_scope.is_department_admin(profile),
        "department": profile.get("department"),  # from Microsoft 365: always in scope, can't be removed
        "extra": users.list_admin_departments(profile["id"]),
    }


def _profile_or_404(user_id) -> dict:
    profile = users.get_profile(user_id)
    if profile is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return profile


@router.get("/{user_id}/admin-departments")
async def list_admin_departments_endpoint(user_id: uuid.UUID, _claims: dict = Depends(require_rbac_admin)):
    return await asyncio.to_thread(lambda: _admin_departments(_profile_or_404(user_id)))


@router.post("/{user_id}/admin-departments", status_code=201)
async def add_admin_department_endpoint(
    user_id: uuid.UUID, request: AdminDepartmentRequest, claims: dict = Depends(require_rbac_admin)
):
    """Adds a department to a department admin's scope. It must be a known department (from a user's
    profile or a role mapping); it is stored with that spelling."""

    def run():
        profile = _profile_or_404(user_id)
        if not admin_scope.is_department_admin(profile):
            raise HTTPException(status_code=409, detail="El usuario no es administrador de departamento.")
        wanted = _plain(request.department)
        department = next((d for d in users.known_departments() if _plain(d) == wanted), None)
        if department is None:
            raise HTTPException(status_code=422, detail="Departamento desconocido.")
        if _plain(profile.get("department") or "") == wanted:
            raise HTTPException(status_code=409, detail="Es el departamento del usuario: ya lo administra.")
        if any(_plain(d["department"]) == wanted for d in users.list_admin_departments(user_id)):
            raise HTTPException(status_code=409, detail="Ya administra ese departamento.")
        try:
            users.add_admin_department(user_id, department, by(claims))
        except APIError as e:
            if getattr(e, "code", None) == "23505":
                raise HTTPException(status_code=409, detail="Ya administra ese departamento.") from None
            raise
        audit.record(claims, "user.admin_department_add", "user", str(user_id), details={"department": department})
        return _admin_departments(profile)

    return await asyncio.to_thread(run)


@router.delete("/{user_id}/admin-departments/{department}")
async def remove_admin_department_endpoint(
    user_id: uuid.UUID, department: str, claims: dict = Depends(require_rbac_admin)
):
    def run():
        profile = _profile_or_404(user_id)
        stored = next((d["department"] for d in users.list_admin_departments(user_id)
                       if _plain(d["department"]) == _plain(department)), None)
        if stored is None or not users.remove_admin_department(user_id, stored):
            raise HTTPException(status_code=404, detail="No administra ese departamento.")
        audit.record(claims, "user.admin_department_remove", "user", str(user_id), details={"department": stored})
        return _admin_departments(profile)

    return await asyncio.to_thread(run)


# --- A user's roles (users or rbac area) -------------------------------------------------------------


class RolesContext:
    """What the actor may do with one user's roles."""

    def __init__(self, actor: Access, user_id):
        self.actor = actor
        self.full = actor.can("admin", "admin_area", "rbac")
        self.scope = None if self.full else admin_scope.scope_of(actor)
        self.profile = _profile_or_404(user_id) if self.full else user_in_scope(user_id, self.scope)
        self.user_id = str(self.profile["id"])
        self.target = rbac.accesses_if_active([self.profile])[self.user_id]
        self.policy = rbac.policy()

    def visible(self, role_id: str) -> bool:
        return self.full or delegable(self.policy, role_id)

    def refusal(self, role) -> tuple[int, str] | None:
        """Why the actor can't assign or remove this role on this user, or None."""
        if self.user_id == str(self.actor.user_id):
            return 400, "No puedes cambiar tus propios roles."
        if (role.key == SUPER_ADMIN or self.target.is_super_admin) and not self.actor.is_super_admin:
            return 403, ONLY_SUPER_ADMIN
        if not self.full:
            if admin_scope.protection(self.actor, self.scope, self.profile, self.target):
                return 403, "No puedes cambiar los roles de un administrador."
            if not delegable(self.policy, role.id):
                return 403, "Ese rol no lo puede asignar un administrador de departamento."
        return None

    def check(self, role):
        refused = self.refusal(role)
        if refused:
            raise HTTPException(status_code=refused[0], detail=refused[1])

    def role(self, role_id) -> object:
        role = self.policy.roles.get(str(role_id))
        if role is None or not self.visible(role.id):
            raise HTTPException(status_code=404, detail="Rol no encontrado.")
        return role

    def view(self) -> dict:
        assigned = {str(g["role_id"]): g for g in rbac.list_user_roles(self.user_id)}
        held = {h.role.id: list(h.sources) for h in self.target.roles}
        roles = []
        for role_id in set(assigned) | set(held):
            role = self.policy.roles.get(role_id)
            if role is None or not self.visible(role_id):
                continue
            grant = assigned.get(role_id) or {}
            sources = held.get(role_id, [])
            roles.append({
                "id": role.id, "key": role.key, "name": role.name, "description": role.description,
                "manager_assignable": role.manager_assignable,
                # "assigned", "department:<x>", "position_prefix:<x>", "everyone", "legacy:<env var>"
                "sources": sources,
                "automatic": any(s != "assigned" for s in sources),
                "assigned": bool(grant),
                "expired": bool(grant) and "assigned" not in sources,
                "granted_by": grant.get("granted_by"), "granted_at": grant.get("granted_at"),
                "expires_at": grant.get("expires_at"),
                "removable": bool(grant) and self.refusal(role) is None,
            })
        assignable = [
            {"id": r.id, "key": r.key, "name": r.name, "description": r.description}
            for r in sorted(self.policy.roles.values(), key=lambda r: r.key)
            if self.visible(r.id) and self.refusal(r) is None
            and not (r.id in assigned and "assigned" in held.get(r.id, []))
        ]
        return {"user_id": self.user_id, "manage_all_roles": self.full,
                "roles": sorted(roles, key=lambda r: r["key"]), "assignable": assignable}


@router.get("/{user_id}/roles")
async def user_roles_endpoint(
    user_id: uuid.UUID, _claims: dict = Depends(require_users_or_rbac_admin), actor: Access = Depends(get_access)
):
    """The user's roles with where each comes from, and the roles the actor may assign. A department admin
    only sees manager-assignable roles."""
    return await asyncio.to_thread(lambda: RolesContext(actor, user_id).view())


@router.post("/{user_id}/roles", status_code=201)
async def assign_user_role_endpoint(
    user_id: uuid.UUID, request: RoleAssign,
    claims: dict = Depends(require_users_or_rbac_admin), actor: Access = Depends(get_access),
):
    """Assigns a role, optionally until expires_at (assigning it again changes the expiry)."""

    def run():
        ctx = RolesContext(actor, user_id)
        role = ctx.role(request.role_id)
        ctx.check(role)
        rbac.assign_role(ctx.user_id, role.id, by(claims), request.expires_at)
        audit.record(claims, "role.assign", "user", ctx.user_id,
                     details={"role": role.key, "via": "users" if not ctx.full else "rbac",
                              "expires_at": request.expires_at.isoformat() if request.expires_at else None})
        return RolesContext(actor, user_id).view()

    return await asyncio.to_thread(run)


@router.delete("/{user_id}/roles/{role_id}")
async def remove_user_role_endpoint(
    user_id: uuid.UUID, role_id: uuid.UUID,
    claims: dict = Depends(require_users_or_rbac_admin), actor: Access = Depends(get_access),
):
    """Removes a role given by hand. A role that comes from a mapping can't be removed here (409)."""

    def run():
        ctx = RolesContext(actor, user_id)
        role = ctx.role(role_id)
        ctx.check(role)
        if not any(str(g["role_id"]) == role.id for g in rbac.list_user_roles(ctx.user_id)):
            automatic = [h.sources for h in ctx.target.roles if h.role.id == role.id]
            if automatic:
                raise HTTPException(status_code=409, detail=(
                    f"El rol viene de una asignación automática ({', '.join(automatic[0])}) y no se quita aquí: "
                    "cambia la asignación en Roles y permisos."))
            raise HTTPException(status_code=404, detail="El usuario no tiene ese rol asignado.")
        if role.key == SUPER_ADMIN and rbac.active_super_admins() == [ctx.user_id]:
            raise HTTPException(status_code=409, detail="Es el último super administrador activo.")
        rbac.revoke_role(ctx.user_id, role.id)
        audit.record(claims, "role.revoke", "user", ctx.user_id,
                     details={"role": role.key, "via": "users" if not ctx.full else "rbac"})
        return RolesContext(actor, user_id).view()

    return await asyncio.to_thread(run)
