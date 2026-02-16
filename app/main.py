import os
import uvicorn
import logging
import sys
import json

from dotenv import load_dotenv
from fastapi import (
    FastAPI, 
    Depends, 
    HTTPException, 
    Request, 
    WebSocket, 
    WebSocketDisconnect,
    Query
)
from fastapi.security import (
    HTTPAuthorizationCredentials,
    HTTPBearer,
)
from jose import jwt, JWTError
from datetime import datetime, timedelta, timezone
from auth import exchange_code_for_token, get_user_profile, AUTHORITY, CLIENT_ID
from agent import app_graph

from models.chat_request import ChatRequest

app = FastAPI()
load_dotenv()

# JWT Configuration (For session management)
SECRET_KEY = "your_super_secret_key"
ALGORITHM = "HS256"
oauth2_scheme = HTTPBearer()


def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(hours=12)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user_dept(
    credentials: HTTPAuthorizationCredentials = Depends(oauth2_scheme),
):
    """
    Decodes the JWT to find the user's department.
    Middleware-like dependency for protected routes.
    """
    token = credentials.credentials
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        department = payload.get("department")
        if department is None:
            raise HTTPException(
                status_code=401, detail="Token missing department scope"
            )
        return department
    except JWTError:
        raise HTTPException(status_code=401, detail="Could not validate credentials")


# --- WebSocket Auth Helper ---
async def get_current_user_dept_ws(
    websocket: WebSocket,
    token: str = Query(...)
):
    """
    WebSocket Authentication Dependency.
    Extracts token from query param ?token=...
    If authentication fails, closes the WebSocket connection.
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        department = payload.get("department")
        if department is None:
            # We close with a specific policy violation code if auth fails
            # 1008 is "Policy Violation"
            await websocket.close(code=1008, reason="Token missing department scope")
            return None
        return department
    except JWTError:
        await websocket.close(code=1008, reason="Could not validate credentials")
        return None




# --- Auth Endpoints ---

@app.get("/login")
def login():
    """Generates the Microsoft Login URL."""
    scope = "User.Read offline_access"
    login_url = (
        f"{AUTHORITY}/oauth2/v2.0/authorize"
        f"?client_id={CLIENT_ID}"
        f"&response_type=code"
        f"&redirect_uri=http://localhost:8000/auth/callback"
        f"&response_mode=query"
        f"&scope={scope}"
    )
    return {"login_url": login_url}


@app.get("/auth/callback")
async def auth_callback(code: str):
    """
    1. Exchange code for MS Graph Token
    2. Get User Profile (Department)
    3. Mint internal JWT with department
    """
    # 1. Get MS Token
    ms_token_data = await exchange_code_for_token(code)
    access_token = ms_token_data.get("access_token")

    # 2. Get Department from Graph
    user_profile = await get_user_profile(access_token)

    # 3. Create Session Token (Embed department here!)
    session_token = create_access_token(
        {
            "sub": user_profile.email,
            "name": user_profile.name,
            "department": user_profile.department,
        }
    )

    return {
        "access_token": session_token,
        "token_type": "bearer",
        "user_department": user_profile.department,
    }


@app.post("/chat")
async def chat_endpoint(
    request: ChatRequest, department: str = Depends(get_current_user_dept)
):
    """
    Authorized endpoint.
    1. Validates JWT.
    2. Extracts Department.
    3. Runs Agent with Department Filter.
    """

    print(f"User from Department '{department}' is asking: {request.message}")

    inputs = {
        "question": request.message,
        "user_department": department,  # Security context injection
    }

    # Run the graph
    result = await app_graph.ainvoke(inputs)

    return {"response": result["answer"], "department_context_used": department}


# --- WebSocket Endpoint ---

@app.websocket("/ws/chat")
async def websocket_chat(
    websocket: WebSocket, 
    token: str = Query(...) # Auth token expected in the query string
):
    """
    WebSocket endpoint for chat.
    Client connects to: ws://localhost:8000/ws/chat?token=YOUR_JWT_HERE
    """
    
    # 1. Accept Connection & Validate Auth Manually
    # The `get_current_user_dept_ws` function is used here to perform authentication
    # and will close the connection if authentication fails.
    department = await get_current_user_dept_ws(websocket, token)
    
    # If department is None, it means the authentication failed and the
    # WebSocket connection was already closed by `get_current_user_dept_ws`.
    if not department:
        return 
        
    await websocket.accept() # Accept the connection only after successful authentication
    print(f"WS Connection accepted for Department: {department}")

    try:
        while True:
            # 2. Receive Message from client
            data = await websocket.receive_text()
            
            # (Optional) Attempt to parse the incoming message as JSON,
            # otherwise treat it as a plain text message.
            try:
                message_data = json.loads(data)
                user_message = message_data.get("message", "")
                if not user_message:
                    # If JSON was parsed but 'message' key is missing or empty
                    await websocket.send_text(json.dumps({"type": "error", "content": "Message content missing in JSON."}))
                    continue
            except json.JSONDecodeError:
                # If it's not valid JSON, treat it as plain text
                user_message = data
                if not user_message.strip(): # Check for empty messages
                    await websocket.send_text(json.dumps({"type": "error", "content": "Empty message received."}))
                    continue
            except Exception as e: # Catch other potential issues during parsing
                await websocket.send_text(json.dumps({"type": "error", "content": f"Failed to process message: {str(e)}"}))
                continue
            
            print(f"Department '{department}' asks: {user_message}")

            # 3. Prepare inputs for your Agent (e.g., LangGraph)
            inputs = {
                "question": user_message,
                "user_department": department,
            }
            
            result = await app_graph.ainvoke(inputs)
            answer = result.get("answer", "No answer could be generated.") 
            
            # 4. Send Response back to the client
            response_payload = {
                "type": "answer",
                "content": answer,
                "department_context": department,
                "timestamp": datetime.utcnow().isoformat() + "Z" 
            }
            await websocket.send_text(json.dumps(response_payload))
            
    except WebSocketDisconnect:
        # This exception is raised when the client disconnects
        print(f"Client from Department '{department}' disconnected.")
    except Exception as e:
        # Catch any other unexpected errors during the WebSocket session
        print(f"Unexpected WebSocket error for Department '{department}': {e}")
        # Attempt to send an error message to the client before potentially closing the connection
        try:
            await websocket.send_text(json.dumps({"type": "error", "content": f"An internal server error occurred: {str(e)}"}))
        except RuntimeError as rt_e:
            # If the client is already disconnected, send_text might fail
            print(f"Could not send error message to client: {rt_e}")
        finally:
            await websocket.close(code=1011) 






if __name__ == "__main__":
    # logging.info("Starting Uvicorn server on 0.0.0.0:8000")
    print("Starting Uvicorn server on 0.0.0.0:8000")
    try:
        uvicorn.run("main:app", host="0.0.0.0", reload=True, port=8000)
    except KeyboardInterrupt:
        # logging.log_shutdown("Keyboard interrupt received")
        print("Keyboard interrupt received")
    except Exception as e:
        # logging.critical(f"Failed to start server: {str(e)}", exc_info=True)
        print(f"Failed to start server: {str(e)}")
        sys.exit(1)
