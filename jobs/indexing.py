import argparse
import logging
import os
import sys
import time
from pathlib import Path

# app/ modules use flat imports (`from config import ...`), so put app/ on the path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

import schedule  # noqa: E402
from audio_ingestion import SharePointSync as AudioSync  # noqa: E402
from config import (  # noqa: E402
    SUPABASE_AUDIO_TABLE,
    SUPABASE_SCHEMA,
    SUPABASE_TABLE,
    global_supabase_client,
)
from db_setup import ensure_database  # noqa: E402
from indexer import SharePointSync as DocsSync  # noqa: E402
from indexing_runner import run_indexing  # noqa: E402
from sharepoint_sync import data_dir  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
INTERVAL_HOURS = float(os.getenv("INDEXING_INTERVAL_HOURS", "24"))


def run_all(force=False):
    """Runs docs then audio. One failing job doesn't stop the other."""
    run_indexing(force=force)


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


def report():
    """Read-only: compares files in SharePoint against files that have chunks in the DB."""
    targets = (
        ("docs", DocsSync(data_dir() / "downloads"), SUPABASE_TABLE),
        ("audio", AudioSync(data_dir() / "downloads_audio"), SUPABASE_AUDIO_TABLE),
    )
    for name, syncer, table in targets:
        remote = syncer.list_remote_paths()
        stored = db_filepaths(table)
        missing, orphans = sorted(remote - stored), sorted(stored - remote)
        print(
            f"\n[{name}] SharePoint: {len(remote)} files | DB ({table}): {len(stored)} files"
            f" | missing in DB: {len(missing)} | orphans in DB: {len(orphans)}"
        )
        for label, items in (("missing", missing), ("orphan", orphans)):
            for path in items[:20]:
                print(f"  {label}: {path}")
            if len(items) > 20:
                print(f"  ... and {len(items) - 20} more {label}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SharePoint -> Supabase indexing job")
    parser.add_argument("--once", action="store_true", help="run once and exit")
    parser.add_argument(
        "--full",
        action="store_true",
        help="re-download and re-index every file, ignoring sync state (implies --once)",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="compare SharePoint vs DB file counts (read-only) and exit",
    )
    args = parser.parse_args()

    if args.report:
        report()
    elif args.once or args.full:
        ensure_database()
        run_all(force=args.full)
    else:
        ensure_database()
        # Run at startup (state may be stale), then every INDEXING_INTERVAL_HOURS
        run_all()
        schedule.every(INTERVAL_HOURS).hours.do(run_all)
        while True:
            schedule.run_pending()
            time.sleep(30)
