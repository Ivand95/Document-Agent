"""Admin-managed ingestion settings, stored in Supabase so the API and the indexer (two separate
containers) share them: which call folders are ingested, their department, and the run/report state."""
import logging
from datetime import datetime, timezone

from config import SUPABASE_SCHEMA, department_options, global_supabase_client
from ingestion_control import (  # noqa: F401  (state keys, re-exported for the API)
    LAST_RUN,
    REPORT,
    REPORT_REQUEST,
    RUN_REQUEST,
    RUN_STATUS,
)

log = logging.getLogger("ingestion_config")

FOLDERS_TABLE = "ingestion_folders"
STATE_TABLE = "ingestion_state"

def _now():
    return datetime.now(timezone.utc).isoformat()


def _table(name):
    return global_supabase_client.schema(SUPABASE_SCHEMA).table(name)


def valid_departments() -> set:
    return {o["code"] for o in department_options()}


# --- Folders ---
def load_folders() -> dict:
    """{folder name: {"enabled": bool, "department": code | None}}; empty until the panel saves once."""
    rows = _table(FOLDERS_TABLE).select("name,enabled,department").execute().data
    return {r["name"]: {"enabled": r["enabled"], "department": r["department"]} for r in rows}


def save_folders(items: list, user: str) -> list:
    """Upserts the folders and relabels the stored calls of those whose department changed.
    Returns the names whose department changed."""
    before = load_folders()
    allowed = valid_departments()
    rows, changed = [], []
    for item in items:
        name = str(item["name"]).strip()
        department = item.get("department") or None
        if not name or "/" in name or "\\" in name:
            raise ValueError(f"Invalid folder name '{name}'")
        if department is not None and department not in allowed:
            raise ValueError(f"Unknown department '{department}'")
        rows.append(
            {"name": name, "enabled": bool(item.get("enabled")), "department": department,
             "updated_at": _now(), "updated_by": user}
        )
        if name in before and before[name]["department"] != department:
            changed.append((name, department))
        elif name not in before and department is not None:
            changed.append((name, department))
    if rows:
        _table(FOLDERS_TABLE).upsert(rows, on_conflict="name").execute()
    for name, department in changed:
        global_supabase_client.schema(SUPABASE_SCHEMA).rpc(
            "set_audio_department", {"folder": name, "dept": department}
        ).execute()
    return [name for name, _ in changed]


def folder_filter(folders: dict):
    """Predicate on a top-level folder name, or None when nothing was configured (ingest everything)."""
    if not folders:
        return None
    return lambda name: folders.get(name, {}).get("enabled", False)


# --- State ---
def get_state(key, default=None):
    rows = _table(STATE_TABLE).select("value,updated_at").eq("key", key).execute().data
    if not rows:
        return default
    return rows[0]["value"]


def set_state(key, value):
    _table(STATE_TABLE).upsert(
        {"key": key, "value": value, "updated_at": _now()}, on_conflict="key"
    ).execute()


def clear_state(key):
    _table(STATE_TABLE).delete().eq("key", key).execute()
