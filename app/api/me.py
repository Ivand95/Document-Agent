"""The signed-in user's own information."""
from fastapi import APIRouter, Depends

from api.deps import get_access
from rbac.model import Access

router = APIRouter(prefix="/me")


@router.get("/permissions")
def my_permissions(access: Access = Depends(get_access)):
    """Roles, agents, admin areas and document categories this user may use. The UI uses it to decide
    which screens to show; every API call checks again on its own."""
    return access.summary()
