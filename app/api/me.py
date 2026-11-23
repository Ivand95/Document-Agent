"""The signed-in user's own information."""
import logging

from fastapi import APIRouter, Depends

from api.deps import get_access
from capabilities import gateway
from rbac.model import Access

log = logging.getLogger("main")

router = APIRouter(prefix="/me")


@router.get("/permissions")
def my_permissions(access: Access = Depends(get_access)):
    """Roles, agents, admin areas, document categories and capabilities this user may use. The UI uses it
    to decide which screens to show; every API call checks again on its own."""
    summary = access.summary()
    try:
        summary["capabilities"] = gateway.allowed_capabilities(access)
    except Exception:  # the registry is unreadable: show nothing rather than fail the whole page
        log.exception("Could not read the capability registry for /me/permissions")
        summary["capabilities"] = []
    return summary
