"""Admin endpoints: manual indexing (non-production only) and the ingestion panel (Tecnología only)."""

import logging
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from api.deps import get_current_user_dept, require_admin, require_non_production
from core.config import department_options
from ingestion import ingestion_config
from ingestion.indexing_runner import indexing_status, start_indexing
from ingestion.indexing_window import IndexingWindow
from ingestion.sharepoint_sync import normalize_folder
from models.indexing_request import IndexingRequest
from models.ingestion_admin import FoldersRequest, RunRequest

log = logging.getLogger("main")

router = APIRouter(prefix="/admin")


# --- Manual indexing (non-production only) ---


@router.post(
    "/indexing/run",
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


@router.get(
    "/indexing/status",
    dependencies=[Depends(require_non_production)],
)
async def indexing_status_endpoint(
    department: str = Depends(get_current_user_dept),
):
    """Current or last manual indexing run."""
    return indexing_status()


# --- Ingestion panel (Tecnología only) ---


def window_info() -> dict:
    window = IndexingWindow.from_env()
    return {
        "start": window.start.strftime("%H:%M"),
        "end": window.end.strftime("%H:%M"),
        "tz": str(window.tz),
        "open_now": window.is_open(),
    }


@router.get("/ingestion/config")
def ingestion_config_endpoint(_claims: dict = Depends(require_admin)):
    """Audio root folder (from .env), its subfolders and the selection saved from the panel."""
    saved = ingestion_config.load_folders()
    found = set((ingestion_config.get_state(ingestion_config.REPORT) or {}).get("audio_folders", []))
    folders = [
        {
            "name": name,
            "enabled": saved.get(name, {}).get("enabled", False),
            "department": saved.get(name, {}).get("department"),
            "found": name in found,  # False: saved earlier but no longer in SharePoint
        }
        for name in sorted(found | saved.keys(), key=lambda n: (not n.isdigit(), int(n) if n.isdigit() else 0, n))
    ]
    return {
        "audio_root": normalize_folder(os.getenv("OFFICE_365_CONVERSATION_FOLDER_PATH", "3CX Recordings")),
        "configured": bool(saved),  # until the first save every folder is ingested
        "folders": folders,
        "departments": department_options(),
        "window": window_info(),
    }


@router.post("/ingestion/folders")
def save_ingestion_folders(request: FoldersRequest, claims: dict = Depends(require_admin)):
    """Saves which folders are ingested and their department (stored in each call's metadata)."""
    try:
        relabeled = ingestion_config.save_folders(
            [f.model_dump() for f in request.folders], claims.get("email") or claims.get("sub") or "?"
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    log.info("Ingestion folders saved (%d folders, %d relabeled)", len(request.folders), len(relabeled))
    return {"saved": len(request.folders), "relabeled": relabeled}


@router.get("/ingestion/report")
def ingestion_report_endpoint(_claims: dict = Depends(require_admin)):
    """Latest pending-files report plus the state of the indexer."""
    get = ingestion_config.get_state
    return {
        "report": get(ingestion_config.REPORT),
        "status": get(ingestion_config.RUN_STATUS),
        "last_run": get(ingestion_config.LAST_RUN),
        "run_request": get(ingestion_config.RUN_REQUEST),
        "report_requested": bool(get(ingestion_config.REPORT_REQUEST)),
        "window": window_info(),
    }


@router.post("/ingestion/report/refresh", status_code=202)
def refresh_ingestion_report(claims: dict = Depends(require_admin)):
    """Asks the indexer for a fresh report (it lists SharePoint, so it can take a minute)."""
    ingestion_config.set_state(ingestion_config.REPORT_REQUEST, {"by": claims.get("sub")})
    return {"status": "requested"}


@router.post("/ingestion/run", status_code=202)
def request_ingestion_run(request: RunRequest, claims: dict = Depends(require_admin)):
    """now: the indexer loads the pending files at its next check, ignoring the window.
    night: cancels a pending "now" request; files are loaded in the nightly window."""
    if request.mode == "now":
        status = ingestion_config.get_state(ingestion_config.RUN_STATUS) or {}
        if status.get("running"):
            raise HTTPException(status_code=409, detail="Ya hay una carga en curso.")
        ingestion_config.set_state(
            ingestion_config.RUN_REQUEST,
            {"mode": "now", "by": claims.get("sub"), "at": datetime.now(timezone.utc).isoformat()},
        )
        log.info("Ingestion run requested for now by '%s'", claims.get("department"))
    else:
        ingestion_config.clear_state(ingestion_config.RUN_REQUEST)
    return {"mode": request.mode}
