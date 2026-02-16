import os
import uvicorn
import logging
import sys
from dotenv import load_dotenv
from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.security import (
    HTTPAuthorizationCredentials,
    OAuth2PasswordBearer,
    HTTPBearer,
)
from jose import jwt, JWTError
from datetime import datetime, timedelta

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
    expire = datetime.utcnow() + timedelta(minutes=60)
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
