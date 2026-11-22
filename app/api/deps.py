"""FastAPI dependencies shared by the routers (authentication and access checks)."""

import logging

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError

from core.security import decode_token
from ingestion.indexing_runner import indexing_endpoints_enabled
from rbac import audit
from rbac import service as rbac
from rbac.model import Access

log = logging.getLogger("main")

oauth2_scheme = HTTPBearer()

LOGIN_AGAIN = "Tu sesión no es válida. Inicia sesión de nuevo."
NOT_ACTIVE = "Tu cuenta no está activa."
UNAVAILABLE = "No se pudieron comprobar tus permisos. Intenta de nuevo en unos minutos."
FORBIDDEN = "No tienes permiso para esta sección."


def get_current_user_claims(
    credentials: HTTPAuthorizationCredentials = Depends(oauth2_scheme),
):
    """
    Decodes the JWT and returns its claims (department is required).
    Middleware-like dependency for protected routes.
    """
    try:
        payload = decode_token(credentials.credentials)
    except JWTError:
        raise HTTPException(status_code=401, detail="Could not validate credentials")
    if payload.get("department") is None:
        raise HTTPException(status_code=401, detail="Token missing department scope")
    return payload


def load_access(claims: dict) -> Access:
    """What the token's user may do now. 401 for tokens without a registered, active user,
    503 if the database can't be read (fail closed)."""
    user_id = claims.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail=LOGIN_AGAIN)
    try:
        access = rbac.access_for(user_id)
    except Exception:
        log.exception("Could not load permissions of user %s", user_id)
        raise HTTPException(status_code=503, detail=UNAVAILABLE)
    if access is None:
        raise HTTPException(status_code=401, detail=LOGIN_AGAIN)
    if not access.active:
        raise HTTPException(status_code=401, detail=NOT_ACTIVE)
    return access


def get_access(claims: dict = Depends(get_current_user_claims)) -> Access:
    return load_access(claims)


def require(action: str, resource_type: str, resource_id: str):
    """Dependency for a route that needs one permission. Returns the token claims.
    Refusals to admin areas are written to the audit log."""

    def dependency(claims: dict = Depends(get_current_user_claims), access: Access = Depends(get_access)) -> dict:
        decision = access.check(action, resource_type, resource_id)
        if not decision.allowed:
            log.warning("Access denied: user %s, %s %s:%s (%s)",
                        access.user_id, action, resource_type, resource_id, decision.reason)
            if resource_type == "admin_area":
                audit.record(claims, "access.denied", resource_type, resource_id, outcome="denied",
                             details={"action": action, "reason": decision.reason})
            raise HTTPException(status_code=403, detail=FORBIDDEN)
        return claims

    dependency.__name__ = f"require_{action}_{resource_type}_{resource_id}"
    return dependency


def require_non_production():
    """Blocks the endpoint unless APP_ENV is a development environment."""
    if not indexing_endpoints_enabled():
        raise HTTPException(
            status_code=403,
            detail="Indexing endpoints are disabled in production.",
        )
