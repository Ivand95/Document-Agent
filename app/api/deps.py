"""FastAPI dependencies shared by the routers (authentication and access checks)."""

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError

from core.config import is_admin_department
from core.security import decode_token
from ingestion.indexing_runner import indexing_endpoints_enabled

oauth2_scheme = HTTPBearer()


def get_current_user_claims(
    credentials: HTTPAuthorizationCredentials = Depends(oauth2_scheme),
):
    """
    Decodes the JWT and returns its claims (department and position are required).
    Middleware-like dependency for protected routes.
    """
    try:
        payload = decode_token(credentials.credentials)
    except JWTError:
        raise HTTPException(status_code=401, detail="Could not validate credentials")
    if payload.get("department") is None:
        raise HTTPException(status_code=401, detail="Token missing department scope")
    return payload


def get_current_user_dept(claims: dict = Depends(get_current_user_claims)):
    return claims["department"]


def require_admin(claims: dict = Depends(get_current_user_claims)):
    """The configuration panel is only for the departments in ADMIN_DEPARTMENTS (default Tecnología)."""
    if not is_admin_department(claims.get("department")):
        raise HTTPException(status_code=403, detail="Solo disponible para el departamento de Tecnología.")
    return claims


def require_non_production():
    """Blocks the endpoint unless APP_ENV is a development environment."""
    if not indexing_endpoints_enabled():
        raise HTTPException(
            status_code=403,
            detail="Indexing endpoints are disabled in production.",
        )
