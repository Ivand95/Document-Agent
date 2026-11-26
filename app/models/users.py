from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class UserStatusRequest(BaseModel):
    status: Literal["active", "pending", "disabled"]


class AdminDepartmentRequest(BaseModel):
    department: str = Field(min_length=1, max_length=100)


class UserSearchRequest(BaseModel):
    """Body of POST /admin/users/search: the search text (names, emails) stays out of URLs and access logs."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["active", "pending", "disabled"] | None = None
    q: str | None = Field(None, max_length=200)
    department: str | None = Field(None, max_length=100)
    limit: int = Field(50, ge=1, le=500)
    offset: int = Field(0, ge=0)
