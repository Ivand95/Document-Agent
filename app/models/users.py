from typing import Literal

from pydantic import BaseModel


class UserStatusRequest(BaseModel):
    status: Literal["active", "pending", "disabled"]
