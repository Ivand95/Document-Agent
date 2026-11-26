from typing import Literal

from pydantic import BaseModel, Field

from capabilities.catalog import KEY_PATTERN, OPERATION_PATTERN

DataClass = Literal["public", "internal", "sensitive"]
Risk = Literal["read", "write", "financial"]


class CapabilityCreate(BaseModel):
    key: str = Field(pattern=KEY_PATTERN)
    kind: Literal["mcp", "cli"]
    name: str = Field(min_length=1, max_length=100)
    description: str = Field("", max_length=2000)
    risk: Risk = "read"
    data_class: DataClass = "sensitive"
    config: dict = Field(default_factory=dict)


class CapabilityUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = Field(None, max_length=2000)
    enabled: bool | None = None
    risk: Risk | None = None
    data_class: DataClass | None = None
    config: dict | None = None


class OperationCreate(BaseModel):
    operation: str = Field(pattern=OPERATION_PATTERN)
    description: str = Field("", max_length=2000)
    arg_schema: dict = Field(default_factory=lambda: {"type": "object", "properties": {}})
    limits: dict = Field(default_factory=dict)
    read_only: bool = False


class OperationUpdate(BaseModel):
    enabled: bool | None = None
    read_only: bool | None = None
    description: str | None = Field(None, max_length=2000)
    arg_schema: dict | None = None
    limits: dict | None = None


class ConnectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    auth_type: Literal["none", "token", "oauth", "dsn"]
    config: dict = Field(default_factory=dict)
    scopes: list[str] = Field(default_factory=list, max_length=50)


class ConnectionUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    config: dict | None = None
    scopes: list[str] | None = Field(None, max_length=50)
    status: Literal["disabled", "unchecked"] | None = None


class OAuthStart(BaseModel):
    # Only for providers without dynamic client registration: a client registered by hand there.
    # Write-only: kept in the connection's encrypted secret, never returned or logged.
    client_id: str | None = Field(None, min_length=1, max_length=300)
    client_secret: str | None = Field(None, min_length=1, max_length=500)


class SecretSet(BaseModel):
    # Write-only: never echoed back, never logged
    secret: dict[str, str] = Field(min_length=1, max_length=10)
