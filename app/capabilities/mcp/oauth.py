"""OAuth 2.0 "service connection" for MCP servers that only accept OAuth (Meta, GoHighLevel...).

An admin presses Connect: the app runs the MCP SDK's OAuth client (protected-resource and authorization
server discovery, dynamic client registration unless a client id is given, PKCE S256, RFC 8707 resource,
issuer checks) and hands the authorization URL to the admin's popup. The provider redirects back to
/admin/capabilities/oauth/callback with the code; the flow exchanges it and stores the tokens in the
connection's secret (encrypted, never returned). Later calls use those tokens and refresh them when they
expire; if the refresh token stops working, the call fails and the connection asks to be connected again.

Pending flows live in this process's memory (like the login's state), for FLOW_SECONDS. With several
replicas, the callback must reach the replica that started the flow (sticky sessions), or the admin
retries.
"""
import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlsplit

from mcp.client.auth import OAuthClientProvider
from mcp.shared.auth import (
    AuthorizationCodeResult, OAuthClientInformationFull, OAuthClientMetadata, OAuthMetadata, OAuthToken,
    ProtectedResourceMetadata,
)

from capabilities import registry, secrets
from capabilities.gateway import CapabilityError

log = logging.getLogger("capabilities")

CALLBACK_PATH = "/admin/capabilities/oauth/callback"
FLOW_SECONDS = 600  # the admin has this long to consent in the popup
START_SECONDS = 30  # discovery and registration, before the authorization URL is known
RECONNECT = "La conexión necesita autorizarse de nuevo (Conectar)."


def redirect_uri() -> str:
    """MCP_OAUTH_REDIRECT_URI, or the login's AUTH_REDIRECT_URI origin + CALLBACK_PATH."""
    explicit = os.getenv("MCP_OAUTH_REDIRECT_URI")
    if explicit:
        return explicit
    parts = urlsplit(os.getenv("AUTH_REDIRECT_URI", "http://localhost:8000"))
    return f"{parts.scheme}://{parts.netloc}{CALLBACK_PATH}"


class NeedsReconnect(Exception):
    pass


# --- Token storage: the connection's encrypted secret ---------------------------------------------------


class ConnectionTokenStorage:
    """The SDK's TokenStorage on top of a connection secret. The secret keeps `access_token` and
    `refresh_token` (what the health check expects of an oauth connection) and `oauth`: a JSON text with
    the tokens, their absolute expiry, the client registration and the discovered metadata, so a refresh
    after a restart goes to the right authorization server without asking the admin again."""

    def __init__(self, connection_id: str, state: dict | None = None):
        self.connection_id = connection_id
        self.state = state if state is not None else self._load()
        self.provider: "StoredOAuthProvider | None" = None

    def _load(self) -> dict:
        try:
            secret = registry.read_secret(self.connection_id) or {}
        except secrets.SecretsUnavailable as e:
            raise CapabilityError("not_available", str(e)) from None
        try:
            return json.loads(secret.get("oauth") or "{}")
        except json.JSONDecodeError:
            return {}

    async def get_tokens(self) -> OAuthToken | None:
        tokens = self.state.get("tokens")
        return OAuthToken.model_validate(tokens) if tokens else None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        self.state["tokens"] = tokens.model_dump(mode="json", exclude_none=True)
        self.state["expires_at"] = time.time() + int(tokens.expires_in) if tokens.expires_in else None
        await self._save()

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        info = self.state.get("client")
        return OAuthClientInformationFull.model_validate(info) if info else None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        self.state["client"] = client_info.model_dump(mode="json", exclude_none=True)
        await self._save()

    def _snapshot_metadata(self) -> None:
        context = self.provider.context if self.provider else None
        if context is None:
            return
        if context.oauth_metadata is not None:
            self.state["oauth_metadata"] = context.oauth_metadata.model_dump(mode="json", exclude_none=True)
        if context.protected_resource_metadata is not None:
            self.state["resource_metadata"] = context.protected_resource_metadata.model_dump(
                mode="json", exclude_none=True)
        if context.auth_server_url:
            self.state["auth_server_url"] = context.auth_server_url

    async def _save(self) -> None:
        self._snapshot_metadata()
        tokens = self.state.get("tokens") or {}
        secret = {"oauth": json.dumps(self.state, separators=(",", ":"))}
        if tokens.get("access_token"):
            secret["access_token"] = tokens["access_token"]
        if tokens.get("refresh_token"):
            secret["refresh_token"] = tokens["refresh_token"]
        await asyncio.to_thread(registry.store_secret, self.connection_id, secret)


class StoredOAuthProvider(OAuthClientProvider):
    """The SDK provider, restoring what it only keeps in memory (token expiry, discovered metadata) from
    the stored state, so an expired token is refreshed instead of starting a new consent."""

    async def _initialize(self) -> None:
        await super()._initialize()
        state = self.context.storage.state
        if state.get("expires_at"):
            self.context.token_expiry_time = float(state["expires_at"])
        if state.get("oauth_metadata") and self.context.oauth_metadata is None:
            self.context.oauth_metadata = OAuthMetadata.model_validate(state["oauth_metadata"])
        if state.get("resource_metadata") and self.context.protected_resource_metadata is None:
            self.context.protected_resource_metadata = ProtectedResourceMetadata.model_validate(
                state["resource_metadata"])
        if state.get("auth_server_url") and not self.context.auth_server_url:
            self.context.auth_server_url = state["auth_server_url"]


def _client_metadata(connection: dict) -> OAuthClientMetadata:
    scopes = " ".join(connection.get("scopes") or []) or None
    return OAuthClientMetadata(
        client_name="Asistente de la cooperativa", redirect_uris=[redirect_uri()],
        grant_types=["authorization_code", "refresh_token"], response_types=["code"], scope=scopes,
    )


def _provider(target, storage: ConnectionTokenStorage, redirect_handler, callback_handler) -> StoredOAuthProvider:
    provider = StoredOAuthProvider(
        server_url=target.capability.config["url"], client_metadata=_client_metadata(target.connection),
        storage=storage, redirect_handler=redirect_handler, callback_handler=callback_handler,
    )
    storage.provider = provider
    return provider


def provider_for(target) -> StoredOAuthProvider:
    """For calls: uses and refreshes the stored tokens; never starts a consent (that is the admin's)."""

    async def refuse_redirect(_url: str) -> None:
        raise NeedsReconnect(RECONNECT)

    async def no_callback() -> AuthorizationCodeResult:  # never reached: the redirect refuses first
        raise NeedsReconnect(RECONNECT)

    storage = ConnectionTokenStorage(target.connection["id"])
    if not storage.state.get("tokens"):
        raise CapabilityError("not_available", RECONNECT, message=RECONNECT)
    return _provider(target, storage, refuse_redirect, no_callback)


# --- The connect flow ----------------------------------------------------------------------------------


@dataclass
class Flow:
    connection_id: str
    capability_key: str
    opener_origin: str | None
    claims: dict
    code: asyncio.Future = field(default_factory=lambda: asyncio.get_running_loop().create_future())
    task: asyncio.Task | None = None
    expires: float = field(default_factory=lambda: time.time() + FLOW_SECONDS)


_flows: dict[str, Flow] = {}  # OAuth state -> flow


def _drop_expired() -> None:
    now = time.time()
    for state in [s for s, f in _flows.items() if f.expires < now]:
        flow = _flows.pop(state)
        if flow.task and not flow.task.done():
            flow.task.cancel()


async def start(target, opener_origin: str | None, claims: dict, client_id: str | None = None,
                client_secret: str | None = None) -> str | None:
    """Starts a consent and returns the authorization URL for the admin's popup (None if the server
    needed no consent). Any stored tokens are replaced when it completes. `client_id`/`client_secret`:
    a client registered by hand at the provider, for servers without dynamic registration."""
    from capabilities.mcp import transport

    _drop_expired()
    state: dict = {}
    if client_id:
        info = {"client_id": client_id, "redirect_uris": [redirect_uri()],
                "token_endpoint_auth_method": "client_secret_post" if client_secret else "none"}
        if client_secret:
            info["client_secret"] = client_secret
        state["client"] = info
    else:  # keep an earlier registration with this server, if any
        previous = ConnectionTokenStorage(target.connection["id"]).state
        if previous.get("client"):
            state["client"] = previous["client"]
    storage = ConnectionTokenStorage(target.connection["id"], state)
    flow = Flow(target.connection["id"], target.capability.key, opener_origin, claims)
    url_ready: asyncio.Future = asyncio.get_running_loop().create_future()

    async def on_redirect(url: str) -> None:
        oauth_state = (parse_qs(urlsplit(url).query).get("state") or [""])[0]
        _flows[oauth_state] = flow
        if not url_ready.done():
            url_ready.set_result(url)

    async def on_callback() -> AuthorizationCodeResult:
        return await asyncio.wait_for(asyncio.shield(flow.code), FLOW_SECONDS)

    provider = _provider(target, storage, on_redirect, on_callback)

    async def run() -> int:
        async with transport.open_client(target, auth=provider, timeout=60) as client:
            return len((await client.list_tools()).tools)

    flow.task = asyncio.create_task(run())
    done, _ = await asyncio.wait({url_ready, flow.task}, timeout=START_SECONDS, return_when=asyncio.FIRST_COMPLETED)
    if url_ready in done:
        return url_ready.result()
    if flow.task in done:
        error = flow.task.exception()
        if error is None:
            return None  # the server let us in without a consent
        log.warning("OAuth start for %s failed: %s", target.capability.key, type(error).__name__)
        raise CapabilityError("failed", f"oauth start: {type(error).__name__}",
                              message="No se pudo iniciar la autorización con el servidor.")
    flow.task.cancel()
    raise CapabilityError("timeout", "oauth discovery", message="El servidor no respondió a tiempo.")


async def complete(oauth_state: str | None, code: str | None, iss: str | None, error: str | None) -> tuple:
    """Finishes the flow the callback belongs to. Returns (flow or None, error message or None)."""
    _drop_expired()
    flow = _flows.pop(oauth_state or "", None)
    if flow is None:
        return None, "La autorización expiró o no es válida. Vuelve a pulsar Conectar."
    if error or not code:
        flow.task.cancel()
        return flow, "El proveedor no autorizó la conexión."
    flow.code.set_result(AuthorizationCodeResult(code=code, state=oauth_state, iss=iss))
    try:
        tools = await asyncio.wait_for(flow.task, START_SECONDS)
    except Exception as e:
        log.warning("OAuth completion for %s failed: %s", flow.capability_key, type(e).__name__)
        await asyncio.to_thread(registry.record_check, flow.connection_id, "error", "La autorización falló")
        return flow, "No se pudo completar la autorización."
    await asyncio.to_thread(registry.record_check, flow.connection_id, "ok", f"Conectado: {tools} herramientas")
    return flow, None
