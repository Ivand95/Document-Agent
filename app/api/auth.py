"""Microsoft 365 login (state + PKCE), token refresh and logout."""

import asyncio
import json
import logging
import os
from urllib.parse import parse_qs, urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from jose import JWTError
from starlette.requests import ClientDisconnect

from api.deps import get_current_user_claims
from core.security import (
    consume_login,
    create_access_token,
    new_login,
    origin_allowed,
    refresh_token,
    revoke_token,
)
from integrations.m365 import AUTHORITY, CLIENT_ID, LOGIN_SCOPE, exchange_code_for_token, get_user_profile
from users import service as users

log = logging.getLogger("main")

router = APIRouter()

LOGIN_ERROR = "No se pudo completar el inicio de sesión. Intenta de nuevo."
STATUS_MESSAGES = {
    "pending": "Tu cuenta fue registrada y está pendiente de aprobación por un administrador.",
    "disabled": "Tu cuenta está deshabilitada. Contacta al administrador.",
}
PROFILE_INCOMPLETE = (
    "Tu perfil de Microsoft 365 no tiene departamento o cargo asignado. Contacta a Tecnología para completarlo."
)
REDIRECT_URI = os.getenv("AUTH_REDIRECT_URI", "http://localhost:8000")
# Microsoft's form post carries code, state and a few short fields; anything bigger is refused
MAX_CALLBACK_BODY = 16 * 1024
# The callback page can carry the session token: never cache it, never leak its URL as a referrer
CALLBACK_HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


@router.get("/login")
def login(request: Request):
    """Generates the Microsoft Login URL (with `state` and PKCE)."""
    origin = request.headers.get("origin")
    if not origin_allowed(origin):
        raise HTTPException(status_code=403, detail="Origin not allowed")
    state, challenge = new_login(origin)
    query = urlencode(
        {
            "client_id": CLIENT_ID,
            "response_type": "code",
            "redirect_uri": f"{REDIRECT_URI}/auth/callback",
            "response_mode": "form_post",
            "scope": LOGIN_SCOPE,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    return {"login_url": f"{AUTHORITY}/oauth2/v2.0/authorize?{query}"}


def callback_page(payload: dict, target_origin: str) -> HTMLResponse:
    """Page that hands the result to the window that opened the login popup."""
    # `<` is escaped so a value like "</script>" can't end the script block
    data = json.dumps(payload).replace("<", "\\u003c")
    target = json.dumps(target_origin).replace("<", "\\u003c")
    html = f"""
    <!DOCTYPE html>
    <html>
    <head><title>Autenticación completada</title></head>
    <body>
        <p>Autenticación completada. Esta ventana se cerrará automáticamente.</p>
        <script>
            if (window.opener) {{
                window.opener.postMessage({data}, {target});
            }}
            window.close();
        </script>
    </body>
    </html>
    """
    return HTMLResponse(html, headers=CALLBACK_HEADERS)


async def read_callback_form(request: Request) -> dict | None:
    """First value of each field of the urlencoded body, or None if the body is too big or not text.
    (Parsed by hand: python-multipart isn't a dependency.)"""
    body = b""
    try:
        async for chunk in request.stream():
            body += chunk
            if len(body) > MAX_CALLBACK_BODY:
                return None
    except ClientDisconnect:  # the popup was closed mid-request: treat it as a failed login
        return None
    try:
        fields = parse_qs(body.decode("ascii"), max_num_fields=20)
    except (UnicodeDecodeError, ValueError):
        return None
    return {name: values[0] for name, values in fields.items()}


@router.post("/auth/callback", response_class=HTMLResponse)
async def auth_callback(request: Request):
    """
    Microsoft posts the result here (response_mode=form_post), so the code never travels in a URL.
    0. Reads `code`, `state` and `error` from the urlencoded body (never from the query string)
    1. Checks `state` (one-time) and recovers the PKCE verifier
    2. Exchange code for MS Graph Token
    3. Get User Profile (Department)
    4. Register the user on their first login, or refresh their profile (only active users get a token)
    5. Mint internal JWT with department and user id
    6. Return HTML that sends data to the opener window (only to the origin that started the login)
    """
    form = await read_callback_form(request)
    if form is None:
        log.warning("Login rejected: unreadable or oversized callback body")
        return callback_page({"type": "ms-auth-callback", "error": LOGIN_ERROR}, "*")
    code, state, error = form.get("code"), form.get("state"), form.get("error")

    verifier, opener = consume_login(state)
    target = opener or "*"
    failure = {"type": "ms-auth-callback", "error": LOGIN_ERROR}

    if verifier is None or error or not code:
        log.warning("Login rejected: unknown or expired state, or the identity provider returned an error")
        return callback_page(failure, target)

    try:
        ms_token_data = await exchange_code_for_token(code, verifier)
        user_profile = await get_user_profile(ms_token_data.get("access_token"))
        user = await asyncio.to_thread(users.provision, user_profile)

        if not user.active:
            log.info("Login refused: user %s is %s", user.id, user.status)
            return callback_page(
                {"type": "ms-auth-callback", "status": user.status, "error": STATUS_MESSAGES[user.status]}, target
            )
        # Every chat checks department and position, so a token without them would be useless
        if not user_profile.department or not user_profile.position:
            log.info("Login refused: user %s has no department or position in Microsoft 365", user.id)
            return callback_page({"type": "ms-auth-callback", "error": PROFILE_INCOMPLETE}, target)

        session_token = create_access_token(
            {
                "sub": user_profile.email,
                "name": user_profile.name,
                "department": user_profile.department,
                "position": user_profile.position,
                "user_id": user.id,
                "perm_version": user.perm_version,
            }
        )
        payload = {
            "type": "ms-auth-callback",
            "access_token": session_token,
            "name": user_profile.name,
            "email": user_profile.email,
            "department": user_profile.department,
            "position": user_profile.position,
        }
    except Exception:
        log.exception("Login failed")  # details stay in the server log
        payload = failure

    return callback_page(payload, target)


@router.post("/auth/refresh")
async def auth_refresh(claims: dict = Depends(get_current_user_claims)):
    """New short-lived token for the same session (it can't outlive SESSION_MAX_HOURS).
    Only while the user is still active; tokens from before user registration must log in again."""
    user_id = claims.get("user_id")
    try:
        user = await asyncio.to_thread(users.get_user, user_id) if user_id else None
    except Exception:
        log.exception("Could not check the user on refresh")
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    if user is None or not user.active:
        raise HTTPException(status_code=401, detail="Session expired")
    try:
        return {"access_token": refresh_token(claims, perm_version=user.perm_version)}
    except JWTError:
        raise HTTPException(status_code=401, detail="Session expired")


@router.post("/auth/logout")
async def auth_logout(claims: dict = Depends(get_current_user_claims)):
    """Invalidates the current token (open WebSockets stop answering with it)."""
    revoke_token(claims)
    return {"status": "ok"}
