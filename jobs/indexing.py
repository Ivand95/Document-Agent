import argparse
import logging
import os
import sys
import time
from pathlib import Path

# app/ modules use flat imports (`from config import ...`), so put app/ on the path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

from db_setup import ensure_database  # noqa: E402
import ingestion_config  # noqa: E402
from indexing_window import IndexingWindow  # noqa: E402
from ingestion_control import IngestionController  # noqa: E402
from indexing_runner import run_indexing  # noqa: E402
from ingestion_report import compute_report  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
CHECK_SECONDS = 60  # how often the loop looks at the clock


def run_all(force=False, target="both", audio_folder=None, should_continue=None):
    """Runs docs and/or audio. One failing job doesn't stop the other."""
    return run_indexing(
        target=target, force=force, audio_folder=audio_folder, should_continue=should_continue
    )


def report():
    """Read-only: compares files in SharePoint against files that have chunks in the DB."""
    data = compute_report()
    for name in ("docs", "audio"):
        info = data[name]
        print(
            f"\n[{name}] SharePoint: {info['remote']} files | indexed: {info['indexed']}"
            f" | pending: {info['pending']} | orphans in DB: {info['orphans']}"
        )
        for path in info["pending_files"][:20]:
            print(f"  pending: {path}")
        if info["pending"] > 20:
            print(f"  ... and {info['pending'] - 20} more pending")


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
    parser.add_argument(
        "--target",
        choices=("both", "docs", "audio"),
        default="both",
        help="what to index (default: both)",
    )
    parser.add_argument(
        "--audio-folder",
        help='audio folder in the library (e.g. "3CX Recordings/recordings/1031"); '
        "default: OFFICE_365_CONVERSATION_FOLDER_PATH",
    )
    args = parser.parse_args()

    if args.report:
        report()
    elif args.once or args.full:
        ensure_database()
        run_all(args.full, args.target, args.audio_folder)
    else:
        ensure_database()
        # Scheduled runs only happen inside the nightly window (INDEXING_WINDOW_START/END, INDEXING_TZ)
        window = IndexingWindow.from_env()
        logging.info(
            "Indexing window %s-%s %s", window.start, window.end, window.tz
        )
        controller = IngestionController(
            lambda should_continue: run_all(
                target=args.target, audio_folder=args.audio_folder, should_continue=should_continue
            ),
            compute_report,
            ingestion_config,
            window,
            report_minutes=float(os.getenv("INGESTION_REPORT_MINUTES", "120")),
        )
        while True:
            controller.tick()
            time.sleep(CHECK_SECONDS)
