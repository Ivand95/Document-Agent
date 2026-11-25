from typing import Literal

from pydantic import BaseModel, Field

DataClass = Literal["public", "internal", "sensitive"]


class ProviderUpdate(BaseModel):
    enabled: bool


class ProviderKey(BaseModel):
    # Write-only: stored encrypted, never echoed back or logged
    api_key: str = Field(min_length=8, max_length=500)


class ModelCreate(BaseModel):
    """OpenAI models are added by hand (their list has no prices); OpenRouter's come from discovery."""
    provider: Literal["openai"]
    model_id: str = Field(min_length=1, max_length=150, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")
    name: str = Field("", max_length=200)
    price_in: float | None = Field(None, ge=0, le=10_000)
    price_out: float | None = Field(None, ge=0, le=10_000)
    supports_tools: bool = True
    supports_json_schema: bool = True
    context_length: int | None = Field(None, ge=1, le=100_000_000)


class ModelUpdate(BaseModel):
    enabled: bool | None = None
    max_data_class: DataClass | None = None
    routing: dict | None = None
    name: str | None = Field(None, max_length=200)
    # Only for models added by hand (discovery overwrites them for OpenRouter)
    price_in: float | None = Field(None, ge=0, le=10_000)
    price_out: float | None = Field(None, ge=0, le=10_000)
    supports_tools: bool | None = None
    supports_json_schema: bool | None = None
