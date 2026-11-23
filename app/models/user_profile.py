from typing import Optional

from pydantic import BaseModel, field_validator


class UserProfile(BaseModel):
    id: str
    name: str
    email: str
    # No fallbacks: a missing department or position must stay missing, not become "General" or "IT"
    department: Optional[str] = None
    position: Optional[str] = None

    @field_validator("department", "position", mode="before")
    @classmethod
    def blank_is_missing(cls, value):
        if isinstance(value, str):
            value = value.strip()
        return value or None
