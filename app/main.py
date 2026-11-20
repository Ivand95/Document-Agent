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

from api import auth, chat  # noqa: E402
from api.admin import ingestion as admin_ingestion  # noqa: E402
from core.security import frontend_origins, validate_settings  # noqa: E402
from db.db_setup import ensure_database  # noqa: E402


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Refuses to start in production with an unsafe JWT secret or CORS origin
    validate_settings()
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
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(auth.router)
app.include_router(chat.router)
app.include_router(admin_ingestion.router)


if __name__ == "__main__":
    print("Starting Uvicorn server on 0.0.0.0:8000")
    try:
        uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
    except KeyboardInterrupt:
        print("Keyboard interrupt received")
    except Exception as e:
        print(f"Failed to start server: {str(e)}")
        sys.exit(1)
