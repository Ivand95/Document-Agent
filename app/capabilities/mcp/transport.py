"""Opens an MCP client session for a registered capability.

- streamable_http: the configured URL, with the connection's credentials: none, a token (sent as
  `Authorization: Bearer <token>`, or in the header named by the connection's config `header`), or
  OAuth (capabilities/mcp/oauth.py, refreshed on its own).
- stdio: an executable from CAPABILITY_BIN_DIR (never a path from the database), with a minimal
  environment: PATH, LANG and the connection's secret fields as MCP_SECRET_<FIELD>.
Each call opens its own session and closes it (no pooling yet).
"""
import os
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx2
from mcp import Client, StdioServerParameters
from mcp.client.streamable_http import streamable_http_client
from mcp_types import Implementation

from capabilities import registry, sandbox
from capabilities.gateway import CapabilityError
from capabilities.registry import Capability

BIN_DIR_ENV = sandbox.BIN_DIR_ENV
MAX_SSE_EVENT_BYTES = 2_000_000
CLIENT_INFO = Implementation(name="coop-agent", version="1.0")
HEADER_NAME = re.compile(r"^[A-Za-z0-9-]{1,64}$")


@dataclass(frozen=True)
class Target:
    capability: Capability
    connection: dict | None  # the capability's connection (without the secret), if it has one


def target_for(capability: Capability) -> Target:
    """The capability and its connection: none, or exactly one that isn't disabled (or the one named in
    config "connection")."""
    connections = [c for c in registry.list_connections(capability.id) if c["status"] != "disabled"]
    name = (capability.config or {}).get("connection")
    if name is not None:
        connections = [c for c in connections if c["name"] == name]
    if len(connections) > 1:
        raise CapabilityError("not_available", f"{capability.key}: more than one connection")
    return Target(capability, connections[0] if connections else None)


def _headers(connection: dict | None, secret: dict | None) -> dict:
    if connection is None or connection["auth_type"] == "none":
        return {}
    if connection["auth_type"] == "token":
        token = (secret or {}).get("token")
        if not token:
            raise CapabilityError("not_available", "token connection without a token")
        header = (connection.get("config") or {}).get("header") or "Authorization"
        if not HEADER_NAME.match(header):
            raise CapabilityError("not_available", "bad header name in the connection config")
        return {header: f"Bearer {token}" if header.lower() == "authorization" else token}
    raise CapabilityError("not_available", f"auth type {connection['auth_type']} is not supported for MCP")


def stdio_command(command: str) -> str:
    """The executable inside CAPABILITY_BIN_DIR (capabilities/sandbox.py)."""
    return sandbox.resolve_executable(command)


SETPRIV = "/usr/bin/setpriv"


def stdio_launch(path: str, args: list[str]) -> tuple[str, list[str]]:
    """The MCP SDK starts stdio servers itself and can't switch users: when the app is root, the server
    is started through setpriv as CAPABILITY_RUN_AS_USER with no extra groups (sandbox.run_as refuses
    when that user isn't set)."""
    user = sandbox.run_as()
    if user is None:
        return path, args
    if not os.path.isfile(SETPRIV):
        raise CapabilityError("not_available", "the app runs as root and setpriv is missing")
    return SETPRIV, ["--reuid", str(user.pw_uid), "--regid", str(user.pw_gid), "--clear-groups", "--", path, *args]


def _stdio_env(secret: dict | None) -> dict:
    env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": os.getenv("LANG", "C.UTF-8")}
    for field, value in (secret or {}).items():
        env["MCP_SECRET_" + re.sub(r"[^A-Z0-9]", "_", field.upper())] = value
    return env


@asynccontextmanager
async def open_client(target: Target, *, auth=None, timeout: float = 30):
    """An entered mcp.Client. `auth` overrides the connection's credentials (the OAuth connect flow)."""
    config = target.capability.config or {}
    connection = target.connection
    if config.get("transport", "streamable_http") == "stdio":
        secret = registry.read_secret(connection["id"]) if connection else None
        command, args = stdio_launch(stdio_command(config["command"]), list(config.get("args") or []))
        server = StdioServerParameters(command=command, args=args, env=_stdio_env(secret))
        async with Client(server, read_timeout_seconds=timeout, client_info=CLIENT_INFO) as client:
            yield client
        return

    headers = {}
    if auth is None and connection is not None and connection["auth_type"] == "oauth":
        from capabilities.mcp import oauth

        auth = oauth.provider_for(target)
    elif auth is None:
        secret = registry.read_secret(connection["id"]) if connection and connection["auth_type"] != "none" else None
        headers = _headers(connection, secret)
    http = httpx2.AsyncClient(headers=headers, auth=auth, timeout=httpx2.Timeout(timeout))
    async with http:
        transport = streamable_http_client(config["url"], http_client=http, max_sse_event_size=MAX_SSE_EVENT_BYTES)
        async with Client(transport, read_timeout_seconds=timeout, client_info=CLIENT_INFO) as client:
            yield client
