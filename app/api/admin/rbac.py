"""Admin API for roles, role mappings, the permission matrix and role assignments.

Everything needs `admin` on the `rbac` admin area. On top of that, to stop anyone from raising their
own access:
- nobody changes their own roles or direct permissions;
- only a super_admin touches the super_admin role (assign, revoke, map, edit its permissions), grants
  a permission on every resource of a type ("*"), or changes the roles/permissions of a super_admin;
- the last active super_admin can't be revoked (use the CLI if you really must);
- system roles (the seeded ones) can't be deleted.
Every change is written to the audit log.
"""
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from postgrest.exceptions import APIError

from api.deps import get_access, require
from models.rbac import MappingCreate, PermissionSet, RoleAssign, RoleCreate, RoleUpdate
from rbac import audit
from rbac import service as rbac
from rbac.catalog import SUPER_ADMIN, WILDCARD, catalog
from rbac.model import Access, opens_admin

log = logging.getLogger("rbac")

router = APIRouter(prefix="/admin/rbac")

require_rbac_admin = require("admin", "admin_area", "rbac")

ONLY_SUPER_ADMIN = "Solo un super administrador puede hacer este cambio."
NOT_YOURSELF = "No puedes cambiar tus propios roles o permisos."


def by(claims: dict) -> str:
    return claims.get("sub") or claims.get("user_id") or "?"


def db_errors(error: APIError):
    """Database constraint errors as HTTP errors."""
    code = getattr(error, "code", None)
    if code == "23505":
        raise HTTPException(status_code=409, detail="Ya existe.")
    if code == "23503":
        raise HTTPException(status_code=404, detail="El usuario o el rol no existe.")
    if code in ("23514", "22P02"):
        raise HTTPException(status_code=422, detail="Valor no válido.")
    raise error


def role_or_404(role_id) -> dict:
    role = rbac.get_role(role_id)
    if role is None:
        raise HTTPException(status_code=404, detail="Rol no encontrado.")
    return role


def guard_role(actor: Access, role: dict):
    if role["key"] == SUPER_ADMIN and not actor.is_super_admin:
        raise HTTPException(status_code=403, detail=ONLY_SUPER_ADMIN)


def is_super_admin_user(user_id) -> bool:
    keys = {r["id"]: r["key"] for r in rbac.list_roles()}
    return any(keys.get(g["role_id"]) == SUPER_ADMIN for g in rbac.list_user_roles(user_id))


def guard_user(actor: Access, user_id):
    """The actor may change this user's roles or direct permissions."""
    if str(user_id) == actor.user_id:
        raise HTTPException(status_code=400, detail=NOT_YOURSELF)
    if not rbac.user_exists(user_id):
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")
    if not actor.is_super_admin and is_super_admin_user(user_id):
        raise HTTPException(status_code=403, detail=ONLY_SUPER_ADMIN)


def guard_subject(actor: Access, subject_type: str, subject_id):
    if subject_type == "role":
        guard_role(actor, role_or_404(subject_id))
    else:
        guard_user(actor, subject_id)


# --- Catalog ---------------------------------------------------------------------------------------


@router.get("/catalog")
def catalog_endpoint(_claims: dict = Depends(require_rbac_admin)):
    """Resource types, actions and the known resources, to build the permission matrix."""
    return catalog()


# --- Roles -----------------------------------------------------------------------------------------


@router.get("/roles")
def list_roles_endpoint(_claims: dict = Depends(require_rbac_admin)):
    return {"roles": rbac.list_roles()}


@router.post("/roles", status_code=201)
def create_role_endpoint(request: RoleCreate, claims: dict = Depends(require_rbac_admin)):
    try:
        role = rbac.create_role(request.key, request.name, request.description, by(claims))
    except APIError as e:
        db_errors(e)
    audit.record(claims, "role.create", "role", role["id"], details={"key": role["key"]})
    return role


@router.patch("/roles/{role_id}")
def update_role_endpoint(
    role_id: uuid.UUID, request: RoleUpdate,
    claims: dict = Depends(require_rbac_admin), actor: Access = Depends(get_access),
):
    """Name, description and manager_assignable: the key never changes (code and mappings may rely on it).
    A role that opens an admin area or grants a whole resource type can't be made manager-assignable."""
    current = role_or_404(role_id)
    guard_role(actor, current)
    if request.manager_assignable and opens_admin(rbac.policy(), str(role_id)):
        raise HTTPException(status_code=409, detail="Este rol abre áreas de administración: no lo puede "
                                                    "asignar un administrador de departamento.")
    role = rbac.update_role(role_id, request.name, request.description, by(claims), request.manager_assignable)
    audit.record(claims, "role.update", "role", role_id, details=request.model_dump(exclude_none=True))
    return role


@router.delete("/roles/{role_id}", status_code=204)
def delete_role_endpoint(
    role_id: uuid.UUID, claims: dict = Depends(require_rbac_admin), actor: Access = Depends(get_access)
):
    """Also removes its mappings, assignments and permissions."""
    role = role_or_404(role_id)
    guard_role(actor, role)
    if role["is_system"]:
        raise HTTPException(status_code=400, detail="Los roles del sistema no se pueden eliminar.")
    rbac.delete_role(role_id)
    audit.record(claims, "role.delete", "role", role_id, details={"key": role["key"]})


# --- Mappings (department / position -> role) --------------------------------------------------------


@router.get("/mappings")
def list_mappings_endpoint(_claims: dict = Depends(require_rbac_admin)):
    return {"mappings": rbac.list_mappings()}


@router.post("/mappings", status_code=201)
def add_mapping_endpoint(
    request: MappingCreate, claims: dict = Depends(require_rbac_admin), actor: Access = Depends(get_access)
):
    guard_role(actor, role_or_404(request.role_id))
    try:
        mapping = rbac.add_mapping(request.kind, request.value, request.role_id, by(claims))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except APIError as e:
        db_errors(e)
    audit.record(claims, "mapping.create", "role", request.role_id,
                 details={"kind": mapping["kind"], "value": mapping["value"]})
    return mapping


@router.delete("/mappings/{mapping_id}", status_code=204)
def delete_mapping_endpoint(
    mapping_id: uuid.UUID, claims: dict = Depends(require_rbac_admin), actor: Access = Depends(get_access)
):
    mapping = rbac.get_mapping(mapping_id)
    if mapping is None:
        raise HTTPException(status_code=404, detail="Asignación no encontrada.")
    guard_role(actor, role_or_404(mapping["role_id"]))
    rbac.delete_mapping(mapping_id)
    audit.record(claims, "mapping.delete", "role", mapping["role_id"],
                 details={"kind": mapping["kind"], "value": mapping["value"]})


# --- Permission matrix ------------------------------------------------------------------------------


@router.get("/permissions")
def list_permissions_endpoint(
    subject_type: str | None = Query(None, pattern="^(role|user)$"),
    subject_id: uuid.UUID | None = None,
    resource_type: str | None = Query(None, pattern="^(capability|agent|admin_area|doc_category|model)$"),
    _claims: dict = Depends(require_rbac_admin),
):
    return {"permissions": rbac.list_permissions(subject_type, subject_id, resource_type)}


@router.put("/permissions")
def set_permission_endpoint(
    request: PermissionSet, claims: dict = Depends(require_rbac_admin), actor: Access = Depends(get_access)
):
    """Creates the cell, or replaces its effect and constraints if it exists."""
    guard_subject(actor, request.subject_type, request.subject_id)
    if request.resource_id == WILDCARD and not actor.is_super_admin:
        raise HTTPException(status_code=403, detail=ONLY_SUPER_ADMIN)
    try:
        row = rbac.set_permission(
            request.subject_type, request.subject_id, request.resource_type, request.resource_id,
            request.action, request.effect, request.constraints, by(claims),
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except APIError as e:
        db_errors(e)
    audit.record(claims, "permission.set", request.subject_type, request.subject_id,
                 details=request.model_dump(mode="json", exclude={"subject_type", "subject_id"}))
    return row


@router.delete("/permissions/{permission_id}", status_code=204)
def delete_permission_endpoint(
    permission_id: uuid.UUID, claims: dict = Depends(require_rbac_admin), actor: Access = Depends(get_access)
):
    row = rbac.get_permission(permission_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Permiso no encontrado.")
    guard_subject(actor, row["subject_type"], row["subject_id"])
    if row["resource_id"] == WILDCARD and not actor.is_super_admin:
        raise HTTPException(status_code=403, detail=ONLY_SUPER_ADMIN)
    rbac.delete_permission(permission_id)
    audit.record(claims, "permission.delete", row["subject_type"], row["subject_id"],
                 details={k: row[k] for k in ("resource_type", "resource_id", "action", "effect")})


# --- A user's roles and effective permissions -----------------------------------------------------


@router.get("/users/{user_id}/roles")
def user_roles_endpoint(user_id: uuid.UUID, _claims: dict = Depends(require_rbac_admin)):
    """Roles given by hand. Roles from mappings are in /effective."""
    if not rbac.user_exists(user_id):
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")
    return {"roles": rbac.list_user_roles(user_id)}


@router.post("/users/{user_id}/roles", status_code=201)
def assign_role_endpoint(
    user_id: uuid.UUID, request: RoleAssign,
    claims: dict = Depends(require_rbac_admin), actor: Access = Depends(get_access),
):
    role = role_or_404(request.role_id)
    guard_role(actor, role)
    guard_user(actor, user_id)
    try:
        row = rbac.assign_role(user_id, request.role_id, by(claims), request.expires_at)
    except APIError as e:
        db_errors(e)
    audit.record(claims, "role.assign", "user", user_id,
                 details={"role": role["key"], "expires_at": request.expires_at.isoformat() if request.expires_at else None})
    return row


@router.delete("/users/{user_id}/roles/{role_id}", status_code=204)
def revoke_role_endpoint(
    user_id: uuid.UUID, role_id: uuid.UUID,
    claims: dict = Depends(require_rbac_admin), actor: Access = Depends(get_access),
):
    role = role_or_404(role_id)
    guard_role(actor, role)
    guard_user(actor, user_id)
    if role["key"] == SUPER_ADMIN and rbac.active_super_admins() == [str(user_id)]:
        raise HTTPException(status_code=409, detail="Es el último super administrador activo.")
    if not rbac.revoke_role(user_id, role_id):
        raise HTTPException(status_code=404, detail="El usuario no tiene ese rol asignado.")
    audit.record(claims, "role.revoke", "user", user_id, details={"role": role["key"]})


@router.get("/users/{user_id}/effective")
def effective_permissions_endpoint(user_id: uuid.UUID, _claims: dict = Depends(require_rbac_admin)):
    """Why this user can or cannot use each thing: roles (and where each comes from), rules, decisions."""
    access = rbac.access_for(user_id)
    if access is None:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")
    return access.explain()
