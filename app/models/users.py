from typing import Literal

from pydantic import BaseModel, Field


class UserStatusRequest(BaseModel):
    status: Literal["active", "pending", "disabled"]


class AdminDepartmentRequest(BaseModel):
    department: str = Field(min_length=1, max_length=100)
