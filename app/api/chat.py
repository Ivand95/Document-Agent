"""Chat endpoints: stateless POST /chat and the WebSocket conversations (documents and audio)."""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from jose import JWTError
from langchain_core.messages import HumanMessage

from api.deps import get_access, get_current_user_claims
from capabilities import gateway
from core import llm
from core.security import MAX_MESSAGE_CHARS, decode_token, origin_allowed
from models.chat_request import ChatRequest
from rbac import service as rbac
from rbac.model import Access

log = logging.getLogger("main")

router = APIRouter()

_app_graph = None
_app_audio_graph = None

INTERNAL_ERROR = "Ocurrió un error interno. Código: {cid}"
UNAVAILABLE = "No se pudieron comprobar tus permisos. Intenta de nuevo en unos minutos."
NO_LONGER_ALLOWED = "Ya no tienes acceso a este asistente."
NOT_ALLOWED = "No tienes permiso para usar este asistente."
SESSION_EXPIRED = "Tu sesión expiró. Inicia sesión de nuevo."
# A JSON frame carries the message plus a few small fields
MAX_FRAME_CHARS = MAX_MESSAGE_CHARS * 2 + 512
# How long a freshly opened socket may wait before sending its {"type": "auth"} frame
AUTH_TIMEOUT_SECONDS = 10


def get_app_graph():
    global _app_graph
    if _app_graph is None:
        from agents.documents.graph import app_graph

        _app_graph = app_graph
    return _app_graph


def get_app_audio_graph():
    global _app_audio_graph
    if _app_audio_graph is None:
        from agents.audio.graph import app_audio_graph

        _app_audio_graph = app_audio_graph
    return _app_audio_graph


async def warm_up_graphs():
    for loader in (get_app_graph, get_app_audio_graph):
        try:
            await asyncio.to_thread(loader)
        except Exception:
            log.exception("Could not preload %s; it will load on first use", loader.__name__)


def agent_inputs(message: str, claims: dict, access: Access, audio: bool = False) -> dict:
    """Graph input. Document categories come from RBAC (deny by default: an empty list finds nothing)."""
    inputs = {
        "messages": [HumanMessage(content=message)],
        "user_department": claims.get("department") or "",
        "position": claims.get("position") or "",
    }
    if not audio:
        inputs["allowed_categories"] = access.doc_categories()
    return inputs


@router.post("/chat")
async def chat_endpoint(
    request: ChatRequest,
    claims: dict = Depends(get_current_user_claims),
    access: Access = Depends(get_access),
):
    """
    Authorized, stateless endpoint (use the WebSocket for conversations).
    Needs `use` on the documents agent; searches only the categories RBAC allows.
    """
    if not access.can("use", "agent", "documents"):
        raise HTTPException(status_code=403, detail=NO_LONGER_ALLOWED)
    department = claims["department"]
    log.info("Chat request from department '%s' (%d chars)", department, len(request.message))

    inputs = agent_inputs(request.message, claims, access)
    # The graph has a checkpointer, so every call needs a thread id; a fresh one keeps this stateless.
    # disabled_mcps: MCP servers switched off for this request (agents that use tools leave them out)
    config = {"configurable": {"thread_id": str(uuid.uuid4()),
                               "disabled_mcps": sorted({k for k in request.disabled_mcps if isinstance(k, str)})}}

    with llm.usage_context(user_id=access.user_id, agent_key="documents"):
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


async def socket_access(claims: dict) -> Access | None:
    """The token user's access, or None if they aren't a registered, active user. Raises on database errors."""
    user_id = claims.get("user_id")
    if not user_id:
        return None
    access = await asyncio.to_thread(rbac.access_for, user_id)
    if access is None or not access.active:
        return None
    return access


async def mcp_frame(websocket: WebSocket, frame: dict, access: Access, mcps_off: set) -> None:
    """{"type": "mcps"} lists the user's MCP servers and whether each is on in this conversation;
    {"type": "mcp_toggle", "key": "<capability key>", "enabled": false|true} switches one. Both answer
    {"type": "mcps", "mcps": [{"key", "name", "description", "enabled"}]}. Only MCP servers can be
    switched; the choice lasts for this connection (conversation) only."""
    available = await asyncio.to_thread(gateway.session_toggles, access, frozenset(mcps_off))
    if frame.get("type") == "mcp_toggle":
        key, enabled = frame.get("key"), frame.get("enabled")
        if not isinstance(enabled, bool) or key not in {m["key"] for m in available}:
            await send_error(websocket, "Esa herramienta no está disponible en esta conversación.")
            return
        (mcps_off.discard if enabled else mcps_off.add)(key)
        available = [{**m, "enabled": m["key"] not in mcps_off} for m in available]
    await websocket.send_text(json.dumps({"type": "mcps", "mcps": available}))

class SocketRefused(Exception):
    """The connection must be closed with this code and reason (for the log), after telling the user `message`."""

    def __init__(self, code: int, reason: str, message: str = SESSION_EXPIRED):
        super().__init__(reason)
        self.code, self.reason, self.message = code, reason, message


async def refuse_socket(websocket: WebSocket, refused: SocketRefused):
    """Sends the user-facing error and closes; a client that already left is not an error."""
    try:
        await send_error(websocket, refused.message)
        await websocket.close(code=refused.code, reason=refused.reason)
    except (WebSocketDisconnect, RuntimeError):
        pass


async def receive_auth_token(websocket: WebSocket) -> str:
    """The token from the first frame, which must be {"type": "auth", "token": "<jwt>"} and arrive in time."""
    try:
        data = await asyncio.wait_for(websocket.receive_text(), timeout=AUTH_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        raise SocketRefused(1008, "Authentication timeout")
    except KeyError:  # a binary frame has no text
        raise SocketRefused(1008, "Could not validate credentials")
    if len(data) > MAX_FRAME_CHARS:
        raise SocketRefused(1008, "Could not validate credentials")
    frame = parse_frame(data)
    token = frame.get("token")
    if frame.get("type") != "auth" or not isinstance(token, str) or not token:
        raise SocketRefused(1008, "Could not validate credentials")
    return token


async def authorize_socket(token: str, agent_id: str) -> tuple[dict, Access]:
    """Connect-time checks: a valid token with department and position, of an active user who may use the agent."""
    try:
        claims = decode_token(token)
    except JWTError:
        raise SocketRefused(1008, "Could not validate credentials")
    if claims.get("department") is None or claims.get("position") is None:
        raise SocketRefused(1008, "Token missing department or position scope")

    # RBAC: the user must be active and allowed to use this agent (calls: managers and audio_user by default)
    try:
        access = await socket_access(claims)
    except Exception:
        log.exception("Could not load permissions (WebSocket connect)")
        raise SocketRefused(1011, "Could not check permissions", UNAVAILABLE)
    if access is None:
        raise SocketRefused(1008, "Could not validate credentials")
    if not access.can("use", "agent", agent_id):
        raise SocketRefused(1008, f"Not authorized for {agent_id} conversations", NOT_ALLOWED)
    return claims, access


async def chat_socket(websocket: WebSocket, get_graph, audio: bool):
    """
    Client connects to: ws://host/ws/chat (or /ws/chat/audio), with no token in the URL (URLs end up in
    access logs). Its first frame must be {"type": "auth", "token": "<jwt>"}, sent within
    AUTH_TIMEOUT_SECONDS; the server answers {"type": "ready"}, or sends {"type": "error"} and closes the
    socket (1008, or 1011 when permissions can't be loaded).
    The token is checked again before every message (expiry, revocation, permissions).
    A client renews it without reconnecting by sending another {"type": "auth", "token": "<new token>"}.
    MCP servers can be switched off for this conversation with {"type": "mcp_toggle"} (see mcp_frame).
    """
    if not origin_allowed(websocket.headers.get("origin")):
        await websocket.close(code=1008, reason="Origin not allowed")
        return

    agent_id = "audio" if audio else "documents"
    await websocket.accept()
    try:
        token = await receive_auth_token(websocket)
        claims, access = await authorize_socket(token, agent_id)
    except SocketRefused as refused:
        await refuse_socket(websocket, refused)
        return
    except WebSocketDisconnect:
        return
    department, position = claims["department"], claims["position"]
    try:
        await websocket.send_text(json.dumps({"type": "ready"}))
    except (WebSocketDisconnect, RuntimeError):
        return  # the client left during the handshake

    # Each connection is its own conversation: LangGraph keeps its state under this id
    session_id = str(uuid.uuid4())
    mcps_off: set = set()  # MCP servers the user switched off in this conversation
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
                    await send_error(websocket, SESSION_EXPIRED)
                    await websocket.close(code=1008)
                    return

            if frame.get("type") == "auth":
                try:
                    renewed = decode_token(str(frame.get("token", "")))
                except JWTError:
                    await send_error(websocket, SESSION_EXPIRED)
                    await websocket.close(code=1008)
                    return
                same_user = renewed.get("sub") == claims.get("sub") and renewed.get("user_id") == claims.get("user_id")
                if not same_user or renewed.get("department") is None or renewed.get("position") is None:
                    await websocket.close(code=1008, reason="Could not validate credentials")
                    return
                token, claims = frame["token"], renewed
                department, position = renewed["department"], renewed["position"]
                continue

            if frame.get("type") in ("mcps", "mcp_toggle"):
                try:
                    access = await socket_access(claims)
                    if access is None:
                        await websocket.close(code=1008, reason="Could not validate credentials")
                        return
                    await mcp_frame(websocket, frame, access, mcps_off)
                except Exception:
                    log.exception("[%s] could not list the MCP servers", cid)
                    await send_error(websocket, UNAVAILABLE)
                continue

            user_message = frame.get("message")
            if not isinstance(user_message, str) or not user_message.strip():
                await send_error(websocket, "Mensaje vacío.")
                continue
            if len(user_message) > MAX_MESSAGE_CHARS:
                await send_error(websocket, f"El mensaje es demasiado largo (máximo {MAX_MESSAGE_CHARS} caracteres).")
                continue

            # Permissions are checked again for every question (cached for RBAC_CACHE_SECONDS), so a
            # disabled user or a revoked agent stops working without waiting for the token to expire
            try:
                access = await socket_access(claims)
            except Exception:
                log.exception("[%s] could not load permissions", cid)
                await send_error(websocket, UNAVAILABLE)
                continue
            if access is None or not access.can("use", "agent", agent_id):
                await send_error(websocket, NO_LONGER_ALLOWED)
                await websocket.close(code=1008)
                return

            # The question itself is not logged: it can contain member data
            log.info("[%s] question from department '%s' (%d chars)", cid, department, len(user_message))

            inputs = agent_inputs(user_message, claims, access, audio)
            # Agents that use tools read disabled_mcps and leave those servers out (gateway session_off)
            config = {"configurable": {"thread_id": session_id, "disabled_mcps": sorted(mcps_off)}}

            try:
                with llm.usage_context(user_id=access.user_id, agent_key="audio" if audio else "documents"):
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


@router.websocket("/ws/chat")
async def websocket_chat(websocket: WebSocket):
    await chat_socket(websocket, get_app_graph, audio=False)


@router.websocket("/ws/chat/audio")
async def websocket_chat_audio(websocket: WebSocket):
    await chat_socket(websocket, get_app_audio_graph, audio=True)
