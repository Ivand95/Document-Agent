import asyncio
import os
import uvicorn
import logging
import sys
import json
import uuid

from contextlib import asynccontextmanager
from urllib.parse import urlencode

from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from fastapi import (
    FastAPI,
    Depends,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
    Query,
)
from fastapi.responses import HTMLResponse
from langchain_core.messages import HumanMessage
from fastapi.security import (
    HTTPAuthorizationCredentials,
    HTTPBearer,
)
from jose import JWTError
from datetime import datetime, timezone
from auth import exchange_code_for_token, get_user_profile, AUTHORITY, CLIENT_ID
from config import can_access_audio
from db_setup import ensure_database
from models.chat_request import ChatRequest
from models.indexing_request import IndexingRequest
from indexing_runner import (
    indexing_endpoints_enabled,
    indexing_status,
    start_indexing,
)
from security import (
    MAX_MESSAGE_CHARS,
    consume_login,
    create_access_token,
    decode_token,
    frontend_origins,
    is_production,
    new_login,
    origin_allowed,
    refresh_token,
    revoke_token,
    validate_settings,
)

log = logging.getLogger("main")

_app_graph = None
_app_audio_graph = None

LOGIN_ERROR = "No se pudo completar el inicio de sesión. Intenta de nuevo."
INTERNAL_ERROR = "Ocurrió un error interno. Código: {cid}"
# A JSON frame carries the message plus a few small fields
MAX_FRAME_CHARS = MAX_MESSAGE_CHARS * 2 + 512


def get_app_graph():
    global _app_graph
    if _app_graph is None:
        from agent import app_graph

        _app_graph = app_graph
    return _app_graph


def get_app_audio_graph():
    global _app_audio_graph
    if _app_audio_graph is None:
        from audio_agent import app_audio_graph

        _app_audio_graph = app_audio_graph
    return _app_audio_graph


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Refuses to start in production with an unsafe JWT secret or CORS origin
    validate_settings()
    # Creates the search tables/functions if SUPABASE_DB_URL is set, and logs anything still missing
    await asyncio.to_thread(ensure_database)
    yield


app = FastAPI(lifespan=lifespan)
load_dotenv()


@app.get("/health")
def health_check():
    return {"status": "ok"}


# Add CORS middleware (production requires explicit origins, see validate_settings)
app.add_middleware(
    CORSMiddleware,
    allow_origins=frontend_origins() or ["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

REDIRECT_URI = os.getenv("AUTH_REDIRECT_URI", "http://localhost:8000")
oauth2_scheme = HTTPBearer()


def get_current_user_claims(
    credentials: HTTPAuthorizationCredentials = Depends(oauth2_scheme),
):
    """
    Decodes the JWT and returns its claims (department and position are required).
    Middleware-like dependency for protected routes.
    """
    try:
        payload = decode_token(credentials.credentials)
    except JWTError:
        raise HTTPException(status_code=401, detail="Could not validate credentials")
    if payload.get("department") is None:
        raise HTTPException(status_code=401, detail="Token missing department scope")
    return payload


def get_current_user_dept(claims: dict = Depends(get_current_user_claims)):
    return claims["department"]


# --- Auth Endpoints ---


@app.get("/login")
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
            "response_mode": "query",
            "scope": "User.Read offline_access",
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    return {"login_url": f"{AUTHORITY}/oauth2/v2.0/authorize?{query}"}


def callback_page(payload: dict, target_origin: str) -> str:
    """Page that hands the result to the window that opened the login popup."""
    # `<` is escaped so a value like "</script>" can't end the script block
    data = json.dumps(payload).replace("<", "\\u003c")
    target = json.dumps(target_origin).replace("<", "\\u003c")
    return f"""
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


@app.get("/auth/callback", response_class=HTMLResponse)
async def auth_callback(
    code: str | None = None, state: str | None = None, error: str | None = None
):
    """
    1. Checks `state` (one-time) and recovers the PKCE verifier
    2. Exchange code for MS Graph Token
    3. Get User Profile (Department)
    4. Mint internal JWT with department
    5. Return HTML that sends data to the opener window (only to the origin that started the login)
    """
    verifier, opener = consume_login(state)
    target = opener or "*"
    failure = {"type": "ms-auth-callback", "error": LOGIN_ERROR}

    if verifier is None or error or not code:
        log.warning("Login rejected: unknown or expired state, or the identity provider returned an error")
        return callback_page(failure, target)

    try:
        ms_token_data = await exchange_code_for_token(code, verifier)
        user_profile = await get_user_profile(ms_token_data.get("access_token"))

        session_token = create_access_token(
            {
                "sub": user_profile.email,
                "name": user_profile.name,
                "department": user_profile.department,
                "position": user_profile.position,
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


@app.post("/auth/refresh")
async def auth_refresh(claims: dict = Depends(get_current_user_claims)):
    """New short-lived token for the same session (it can't outlive SESSION_MAX_HOURS)."""
    try:
        return {"access_token": refresh_token(claims)}
    except JWTError:
        raise HTTPException(status_code=401, detail="Session expired")


@app.post("/auth/logout")
async def auth_logout(claims: dict = Depends(get_current_user_claims)):
    """Invalidates the current token (open WebSockets stop answering with it)."""
    revoke_token(claims)
    return {"status": "ok"}


@app.post("/chat")
async def chat_endpoint(
    request: ChatRequest, claims: dict = Depends(get_current_user_claims)
):
    """
    Authorized, stateless endpoint (use the WebSocket for conversations).
    1. Validates JWT.
    2. Extracts Department and Position.
    3. Runs Agent with Department Filter.
    """
    department = claims["department"]
    log.info("Chat request from department '%s' (%d chars)", department, len(request.message))

    inputs = {
        "messages": [HumanMessage(content=request.message)],
        "user_department": department,  # Security context injection
        "position": claims.get("position") or "",
    }
    # The graph has a checkpointer, so every call needs a thread id; a fresh one keeps this stateless
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}

    result = await get_app_graph().ainvoke(inputs, config=config)

    return {"response": result["answer"], "department_context_used": department}


# --- WebSocket chat (documents and audio share the same protocol) ---


def parse_frame(data: str) -> dict:
    """JSON frames are dicts; anything else is a plain-text message."""
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError:
        return {"message": data}
    return parsed if isinstance(parsed, dict) else {"message": data}


async def send_error(websocket: WebSocket, content: str):
    await websocket.send_text(json.dumps({"type": "error", "content": content}))


async def chat_socket(websocket: WebSocket, token: str, get_graph, audio: bool):
    """
    Client connects to: ws://host/ws/chat?token=YOUR_JWT_HERE (or /ws/chat/audio).
    The token is checked on connect and again before every message (expiry, revocation).
    A client renews it without reconnecting by sending {"type": "auth", "token": "<new token>"}.
    """
    if not origin_allowed(websocket.headers.get("origin")):
        await websocket.close(code=1008, reason="Origin not allowed")
        return

    try:
        claims = decode_token(token)
    except JWTError:
        await websocket.close(code=1008, reason="Could not validate credentials")
        return
    department, position = claims.get("department"), claims.get("position")
    if department is None or position is None:
        await websocket.close(code=1008, reason="Token missing department or position scope")
        return

    # Call recordings are sensitive: only managers and explicitly allowed departments
    if audio and not can_access_audio(department, position):
        await websocket.close(code=1008, reason="Not authorized for audio conversations")
        return

    await websocket.accept()
    # Each connection is its own conversation: LangGraph keeps its state under this id
    session_id = str(uuid.uuid4())
    log.info("WS accepted for department '%s' (session %s, audio=%s)", department, session_id, audio)

    try:
        while True:
            data = await websocket.receive_text()
            cid = uuid.uuid4().hex[:8]  # lets a user report an error without exposing details

            if len(data) > MAX_FRAME_CHARS:
                await send_error(websocket, f"El mensaje es demasiado largo (máximo {MAX_MESSAGE_CHARS} caracteres).")
                continue

            frame = parse_frame(data)

            # Expired or revoked tokens end the conversation
            try:
                claims = decode_token(token)
            except JWTError:
                if frame.get("type") != "auth":
                    await send_error(websocket, "Tu sesión expiró. Inicia sesión de nuevo.")
                    await websocket.close(code=1008)
                    return

            if frame.get("type") == "auth":
                try:
                    renewed = decode_token(str(frame.get("token", "")))
                except JWTError:
                    await send_error(websocket, "Tu sesión expiró. Inicia sesión de nuevo.")
                    await websocket.close(code=1008)
                    return
                same_user = renewed.get("sub") == claims.get("sub")
                if not same_user or renewed.get("department") is None or renewed.get("position") is None:
                    await websocket.close(code=1008, reason="Could not validate credentials")
                    return
                token, claims = frame["token"], renewed
                department, position = renewed["department"], renewed["position"]
                if audio and not can_access_audio(department, position):
                    await websocket.close(code=1008, reason="Not authorized for audio conversations")
                    return
                continue

            user_message = frame.get("message")
            if not isinstance(user_message, str) or not user_message.strip():
                await send_error(websocket, "Mensaje vacío.")
                continue
            if len(user_message) > MAX_MESSAGE_CHARS:
                await send_error(websocket, f"El mensaje es demasiado largo (máximo {MAX_MESSAGE_CHARS} caracteres).")
                continue

            # The question itself is not logged: it can contain member data
            log.info("[%s] question from department '%s' (%d chars)", cid, department, len(user_message))

            inputs = {
                "messages": [HumanMessage(content=user_message)],
                "user_department": department,
                "position": position,
            }
            config = {"configurable": {"thread_id": session_id}}

            try:
                result = await get_graph().ainvoke(inputs, config=config)
            except Exception:
                log.exception("[%s] agent failed", cid)
                await send_error(websocket, INTERNAL_ERROR.format(cid=cid))
                continue

            await websocket.send_text(
                json.dumps(
                    {
                        "type": "answer",
                        "content": result.get("answer", "No answer could be generated."),
                        "department_context": department,
                        "position_context": position,
                        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                    }
                )
            )

    except WebSocketDisconnect:
        log.info("Client from department '%s' disconnected (session %s)", department, session_id)
    except Exception:
        log.exception("Unexpected WebSocket error (session %s)", session_id)
        try:
            await send_error(websocket, INTERNAL_ERROR.format(cid=session_id[:8]))
        except RuntimeError:
            pass  # the client is already gone
        finally:
            await websocket.close(code=1011)


@app.websocket("/ws/chat")
async def websocket_chat(websocket: WebSocket, token: str = Query(...)):
    await chat_socket(websocket, token, get_app_graph, audio=False)


@app.websocket("/ws/chat/audio")
async def websocket_chat_audio(websocket: WebSocket, token: str = Query(...)):
    await chat_socket(websocket, token, get_app_audio_graph, audio=True)


# --- Manual indexing (non-production only) ---


def require_non_production():
    """Blocks the endpoint unless APP_ENV is a development environment."""
    if not indexing_endpoints_enabled():
        raise HTTPException(
            status_code=403,
            detail="Indexing endpoints are disabled in production.",
        )


@app.post(
    "/admin/indexing/run",
    status_code=202,
    dependencies=[Depends(require_non_production)],
)
async def run_indexing_endpoint(
    request: IndexingRequest,
    department: str = Depends(get_current_user_dept),
):
    """
    Starts a SharePoint -> Supabase indexing run in the background.
    Only available when APP_ENV is development/dev/local/test.
    Poll GET /admin/indexing/status for the result.
    """
    params = {
        "target": request.target,
        "force": request.full,
        "limit": request.limit,
        "docs_folder": request.docs_folder,
        "audio_folder": request.audio_folder,
    }
    print(f"Indexing run requested by department '{department}': {params}")
    if not start_indexing(params):
        raise HTTPException(status_code=409, detail="An indexing run is already in progress.")
    return {"status": "started", "params": params}


@app.get(
    "/admin/indexing/status",
    dependencies=[Depends(require_non_production)],
)
async def indexing_status_endpoint(
    department: str = Depends(get_current_user_dept),
):
    """Current or last manual indexing run."""
    return indexing_status()


if __name__ == "__main__":
    # logging.info("Starting Uvicorn server on 0.0.0.0:8000")
    print("Starting Uvicorn server on 0.0.0.0:8000")
    try:
        uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
    except KeyboardInterrupt:
        # logging.log_shutdown("Keyboard interrupt received")
        print("Keyboard interrupt received")
    except Exception as e:
        # logging.critical(f"Failed to start server: {str(e)}", exc_info=True)
        print(f"Failed to start server: {str(e)}")
        sys.exit(1)
