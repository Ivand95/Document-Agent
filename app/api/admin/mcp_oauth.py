"""The OAuth callback of MCP service connections (the provider redirects the admin's popup here).

It has no login of its own: a popup navigation carries no token. What ties it to the admin is the OAuth
`state`, unguessable and single-use, created by an authenticated admin in
POST /admin/capabilities/connections/{id}/oauth/start (capabilities/mcp/oauth.py).
"""
from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from api.auth import callback_page
from capabilities.mcp import oauth
from core.security import frontend_origins
from rbac import audit

router = APIRouter()


@router.get(oauth.CALLBACK_PATH, response_class=HTMLResponse)
async def mcp_oauth_callback(code: str | None = None, state: str | None = None, iss: str | None = None,
                             error: str | None = None):
    flow, problem = await oauth.complete(state, code, iss, error)
    payload = {"type": "mcp-oauth-callback", "connection_id": flow.connection_id if flow else None,
               "status": "error" if problem else "ok", "error": problem}
    if flow is not None:
        audit.record(flow.claims, "connection.oauth_connect", "connection", flow.connection_id,
                     outcome="error" if problem else "ok", details={"capability": flow.capability_key})
    origins = frontend_origins()
    target = (flow.opener_origin if flow and flow.opener_origin else None) or (origins[0] if origins else "*")
    return callback_page(payload, target)
