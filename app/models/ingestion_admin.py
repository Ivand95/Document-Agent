from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class FolderSetting(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    enabled: bool = False
    department: Optional[str] = Field(None, max_length=20)  # department code (COM, HR...) or None


class FoldersRequest(BaseModel):
    folders: List[FolderSetting] = Field(..., max_length=2000)


class RunRequest(BaseModel):
    # now: load the pending files at the next check (minutes); night: wait for the nightly window
    mode: Literal["now", "night"]
