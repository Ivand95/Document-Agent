"""Decides when the indexer works: the nightly window, "load now" requests from the admin panel,
and the periodic report. Talks to the panel only through a state store (Supabase, see
ingestion_config), so the API and the indexer don't need to reach each other."""
import logging
import time
from datetime import datetime, timezone

from indexing_window import IndexingWindow, NightlyScheduler

log = logging.getLogger("ingestion_control")

# ingestion_state keys
RUN_REQUEST = "run_request"  # {"mode": "now", "by": ..., "at": ...} written by the panel, consumed here
RUN_STATUS = "run_status"  # {"running": bool, "mode": ..., "started_at": ...}
LAST_RUN = "last_run"  # {"mode", "started_at", "finished_at", "results"}
REPORT = "report"  # computed here, read by the panel
REPORT_REQUEST = "report_request"  # the panel asks for a fresh report


def _now():
    return datetime.now(timezone.utc).isoformat()


class IngestionController:
    """Call `tick()` periodically.

    run(should_continue) -> results   does the indexing (stops when should_continue() is False)
    build_report() -> dict            slow SharePoint vs DB comparison
    store                             get_state / set_state / clear_state
    """

    def __init__(self, run, build_report, store, window: IndexingWindow, report_minutes=120, clock=time.monotonic):
        self.run = run
        self.build_report = build_report
        self.store = store
        self.window = window
        self.report_seconds = report_minutes * 60
        self.clock = clock
        self.last_report = None
        self.night = NightlyScheduler(window, lambda cont: self._run("night", cont))

    # Panel and DB problems must never stop the indexer loop
    def _safe(self, action, *args, default=None):
        try:
            return action(*args)
        except Exception:
            log.exception("State store call failed")
            return default

    def _run(self, mode, should_continue):
        started = _now()
        self._safe(self.store.set_state, RUN_STATUS, {"running": True, "mode": mode, "started_at": started})
        try:
            results = self.run(should_continue)
        except Exception as e:
            log.exception("Indexing run failed")
            results = {"error": str(e)}
        self._safe(
            self.store.set_state,
            LAST_RUN,
            {"mode": mode, "started_at": started, "finished_at": _now(), "results": results},
        )
        self._safe(self.store.set_state, RUN_STATUS, {"running": False, "mode": mode, "started_at": started})
        self.refresh_report()  # what is still pending after this run

    def refresh_report(self):
        try:
            report = self.build_report()
        except Exception as e:
            log.exception("Report failed")
            report = {"error": str(e), "generated_at": _now()}
        self.last_report = self.clock()
        self._safe(self.store.set_state, REPORT, report)
        self._safe(self.store.clear_state, REPORT_REQUEST)

    def tick(self, now=None) -> str | None:
        """Returns what it did: "now", "night", "report" or None."""
        request = self._safe(self.store.get_state, RUN_REQUEST)
        if request and request.get("mode") == "now":
            self._safe(self.store.clear_state, RUN_REQUEST)
            self._run("now", lambda: True)  # explicit request: the window doesn't apply
            return "now"

        if self.night.tick(now):
            return "night"

        due = self.last_report is None or self.clock() - self.last_report >= self.report_seconds
        if due or self._safe(self.store.get_state, REPORT_REQUEST):
            self.refresh_report()
            return "report"
        return None
