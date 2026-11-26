from pydantic import BaseModel, Field

from core.security import MAX_MESSAGE_CHARS


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)
    # MCP servers (capability keys) the user switched off for this request; see GET /me/mcps
    disabled_mcps: list[str] = Field(default_factory=list, max_length=100)
