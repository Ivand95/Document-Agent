from typing import Literal, Optional

from pydantic import BaseModel, Field


class IndexingRequest(BaseModel):
    target: Literal["docs", "audio", "both"] = "both"
    full: bool = False  # ignore sync state and re-index every file
    limit: Optional[int] = Field(None, ge=1, le=100000)  # max files per target
    # None = configured default; "" = whole library
    docs_folder: Optional[str] = None
    audio_folder: Optional[str] = None
