import asyncio
import logging
import os
import threading
import time
from datetime import datetime, timezone

log = logging.getLogger("indexing")

# Environments where the manual indexing endpoints are allowed. Anything else
# (including APP_ENV unset) counts as production, so the endpoints fail closed.
NON_PROD_ENVS = {"development", "dev", "local", "test"}

_lock = threading.Lock()
_state = {"running": False, "params": None, "started_at": None, "finished_at": None, "result": None}


def indexing_endpoints_enabled() -> bool:
    return os.getenv("APP_ENV", "production").strip().lower() in NON_PROD_ENVS


def _now():
    return datetime.now(timezone.utc).isoformat()


def run_indexing(target="both", force=False, limit=None, docs_folder=None, audio_folder=None):
    """Runs docs and/or audio indexing synchronously. One failing target doesn't stop the other."""
    # Imported lazily: both modules load Docling and create clients at import time
    from audio_ingestion import scheduled_audio_indexing
    from indexer import scheduled_indexing

    jobs = {
        "docs": lambda: scheduled_indexing(force, limit, docs_folder),
        "audio": lambda: asyncio.run(scheduled_audio_indexing(force, limit, audio_folder)),
    }
    results = {}
    for name, job in jobs.items():
        if target not in (name, "both"):
            continue
        started = time.time()
        log.info("Indexing job '%s' started", name)
        try:
            results[name] = job()
            log.info("Indexing job '%s' finished in %.0fs: %s", name, time.time() - started, results[name])
        except Exception as e:
            log.exception("Indexing job '%s' failed", name)
            results[name] = {"error": str(e)}
    return results


def start_indexing(params: dict) -> bool:
    """Starts a run in a background thread. Returns False if one is already running."""
    with _lock:
        if _state["running"]:
            return False
        _state.update(running=True, params=params, started_at=_now(), finished_at=None, result=None)

    def work():
        try:
            result = run_indexing(**params)
        except Exception as e:  # e.g. missing config at import time
            log.exception("Indexing run failed")
            result = {"error": str(e)}
        with _lock:
            _state.update(running=False, finished_at=_now(), result=result)

    threading.Thread(target=work, daemon=True, name="indexing-run").start()
    return True


def indexing_status() -> dict:
    with _lock:
        return dict(_state)
