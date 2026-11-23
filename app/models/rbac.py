import json
import uuid
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, field_validator

ROLE_KEY = r"^[a-z][a-z0-9_]{1,62}$"
MAX_CONSTRAINTS_CHARS = 4000


class RoleCreate(BaseModel):
    key: str = Field(pattern=ROLE_KEY)
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(None, max_length=500)


class RoleUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = Field(None, max_length=500)


class MappingCreate(BaseModel):
    kind: Literal["department", "position_prefix", "everyone"]
    value: str = Field("", max_length=200)
    role_id: uuid.UUID


class PermissionSet(BaseModel):
    subject_type: Literal["role", "user"]
    subject_id: uuid.UUID
    resource_type: Literal["capability", "agent", "admin_area", "doc_category", "model"]
    resource_id: str = Field(min_length=1, max_length=200)
    action: Literal["use", "configure", "admin"]
    effect: Literal["allow", "deny"] = "allow"
    constraints: dict = Field(default_factory=dict)

    @field_validator("constraints")
    @classmethod
    def small_constraints(cls, value: dict) -> dict:
        if len(json.dumps(value)) > MAX_CONSTRAINTS_CHARS:
            raise ValueError(f"constraints must be at most {MAX_CONSTRAINTS_CHARS} characters of JSON")
        return value


class RoleAssign(BaseModel):
    role_id: uuid.UUID
    expires_at: datetime | None = None

    @field_validator("expires_at")
    @classmethod
    def future_and_aware(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("expires_at must include a time zone")
        if value <= datetime.now(timezone.utc):
            raise ValueError("expires_at must be in the future")
        return value
