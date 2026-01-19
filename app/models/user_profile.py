from pydantic import BaseModel
from typing import Optional

class UserProfile(BaseModel):
    id: str
    name: str
    email: str
    department: Optional[str] = "General" # Fallback if undefined