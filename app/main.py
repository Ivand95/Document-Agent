import asyncio
import os
import sys
from contextlib import asynccontextmanager

# Load .env before the routers import modules that read settings at import time
from dotenv import load_dotenv

load_dotenv()

import uvicorn  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

from api import auth, chat, me  # noqa: E402
from api.admin import audit as admin_audit  # noqa: E402
from api.admin import ingestion as admin_ingestion  # noqa: E402
from api.admin import rbac as admin_rbac  # noqa: E402
from api.admin import users as admin_users  # noqa: E402
from core.security import frontend_origins, is_production, validate_settings  # noqa: E402
from db.db_setup import ensure_database  # noqa: E402
from integrations.m365 import validate_tenant  # noqa: E402


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Refuses to start in production with an unsafe JWT secret, CORS origin or shared M365 tenant
    validate_settings()
    validate_tenant(is_production())
    # Creates the search tables/functions if SUPABASE_DB_URL is set, and logs anything still missing
    await asyncio.to_thread(ensure_database)
    # Importing the agents (LangGraph, clients) takes seconds: do it now, not on the first question
    warm_up = asyncio.create_task(chat.warm_up_graphs())
    yield
    warm_up.cancel()


app = FastAPI(lifespan=lifespan)


@app.get("/health")
def health_check():
    return {"status": "ok"}


# Add CORS middleware (production requires explicit origins, see validate_settings)
app.add_middleware(
    CORSMiddleware,
    allow_origins=frontend_origins() or ["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(auth.router)
app.include_router(chat.router)
app.include_router(admin_ingestion.router)
app.include_router(admin_users.router)
app.include_router(admin_rbac.router)
app.include_router(admin_audit.router)
app.include_router(me.router)


if __name__ == "__main__":
    print("Starting Uvicorn server on 0.0.0.0:8000")
    try:
        uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
    except KeyboardInterrupt:
        print("Keyboard interrupt received")
    except Exception as e:
        print(f"Failed to start server: {str(e)}")
        sys.exit(1)
