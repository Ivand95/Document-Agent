from pydantic import BaseModel
from typing import Optional, List

class UserProfile(BaseModel):
    id: str
    name: str
    email: str
    department: Optional[str] = "General" # Fallback if undefined
    position: Optional[str] = "IT" # Fallback if undefined