"""Compares SharePoint against what is indexed, to show what is still pending. Read-only."""
from datetime import datetime, timezone
from pathlib import Path

from config import (
    SUPABASE_AUDIO_TABLE,
    SUPABASE_SCHEMA,
    SUPABASE_TABLE,
    global_supabase_client,
)
from ingestion_config import folder_filter, load_folders
from sharepoint_sync import data_dir

MAX_LISTED = 50  # pending files named in the report; the count is always complete


def db_filepaths(table):
    """Distinct `metadata.filepath` values stored in a chunks table."""
    paths, start, page = set(), 0, 1000
    while True:
        rows = (
            global_supabase_client.schema(SUPABASE_SCHEMA)
            .table(table)
            .select("metadata->>filepath")
            .range(start, start + page - 1)
            .execute()
            .data
        )
        paths.update(r["filepath"] for r in rows if r.get("filepath"))
        if len(rows) < page:
            return paths
        start += page


def compare(remote: set, stored: set) -> dict:
    missing, orphans = sorted(remote - stored), sorted(stored - remote)
    return {
        "remote": len(remote),
        "indexed": len(remote & stored),
        "pending": len(missing),
        "orphans": len(orphans),
        "pending_files": missing[:MAX_LISTED],
    }


def audio_report(syncer, stored: set, folders: dict) -> dict:
    """Audio comparison, limited to the selected folders and broken down per folder."""
    syncer.folder_filter = folder_filter(folders)
    remote = syncer.list_remote_paths()
    prefix = str(Path(syncer.download_dir.name) / syncer.root_folder) + "/"

    def folder_of(path):
        rest = path[len(prefix):] if path.startswith(prefix) else path
        return rest.split("/")[0] if "/" in rest else ""

    # Calls of unselected folders stay indexed; they are not "orphans" of this selection
    if syncer.folder_filter:
        stored = {p for p in stored if syncer.folder_filter(folder_of(p))}

    report = compare(remote, stored)
    per_folder = {}
    for name in sorted({folder_of(p) for p in remote | stored}):
        if not name:
            continue
        in_folder = lambda paths: {p for p in paths if folder_of(p) == name}  # noqa: E731
        info = compare(in_folder(remote), in_folder(stored))
        info.pop("pending_files")
        per_folder[name] = info
    report["folders"] = per_folder
    return report


def compute_report() -> dict:
    """Runs the comparison for documents and call recordings. Slow: it lists SharePoint."""
    # Imported here: both modules load Docling and create clients at import time
    from audio_ingestion import SharePointSync as AudioSync
    from indexer import SharePointSync as DocsSync

    docs_syncer = DocsSync(data_dir() / "downloads")
    audio_syncer = AudioSync(data_dir() / "downloads_audio")
    folders = load_folders()

    docs = compare(docs_syncer.list_remote_paths(), db_filepaths(SUPABASE_TABLE))
    audio = audio_report(audio_syncer, db_filepaths(SUPABASE_AUDIO_TABLE), folders)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "docs": docs,
        "audio": audio,
        "audio_folders": audio_syncer.list_subfolders(),
    }
