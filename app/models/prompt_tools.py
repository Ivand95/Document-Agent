from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from capabilities.catalog import KEY_PATTERN

DataClass = Literal["public", "internal", "sensitive"]


class PromptToolSettings(BaseModel):
    """The tool's config. In an update, only the fields sent change; output_schema null removes it."""
    model_config = ConfigDict(extra="forbid")

    model: str | None = Field(None, max_length=220)  # "provider:model", from the allowlist
    template: str | None = Field(None, max_length=20_000)  # Jinja2, sandboxed; inputs by name: {{ text }}
    output_schema: dict | None = None  # JSON Schema of an object; none: the tool answers with text
    max_input_chars: int | None = None
    max_tokens: int | None = None
    timeout_seconds: int | None = None


class PromptToolCreate(BaseModel):
    key: str = Field(pattern=KEY_PATTERN)
    name: str = Field(min_length=1, max_length=100)
    description: str = Field("", max_length=2000)  # what the agent's model reads to decide when to use it
    # What the tool may receive and returns; at or below its model's max_data_class
    data_class: DataClass = "internal"
    config: PromptToolSettings
    input_schema: dict = Field(default_factory=lambda: {"type": "object", "properties": {}})


class PromptToolUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = Field(None, max_length=2000)
    enabled: bool | None = None
    data_class: DataClass | None = None
    config: PromptToolSettings | None = None
    input_schema: dict | None = None


class PromptToolTest(BaseModel):
    args: dict = Field(default_factory=dict)
    # The most restricted kind of data in the sample: stated by the admin, never guessed
    data_class: DataClass
