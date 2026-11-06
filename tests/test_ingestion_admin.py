import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

from fastapi.testclient import TestClient  # noqa: E402

import ingestion_config  # noqa: E402
import main  # noqa: E402
from config import is_admin_department  # noqa: E402
from indexing_window import IndexingWindow  # noqa: E402
from ingestion_control import (  # noqa: E402
    LAST_RUN,
    REPORT,
    REPORT_REQUEST,
    RUN_REQUEST,
    RUN_STATUS,
    IngestionController,
)
from ingestion_report import compare  # noqa: E402
from sharepoint_sync import SharePointSyncBase  # noqa: E402

TZ = ZoneInfo("America/Santo_Domingo")
DAY = datetime(2026, 1, 10, 14, 0, tzinfo=TZ)  # window closed
NIGHT = datetime(2026, 1, 10, 23, 0, tzinfo=TZ)  # window open


class FakeStore:
    def __init__(self, **state):
        self.state = dict(state)

    def get_state(self, key, default=None):
        return self.state.get(key, default)

    def set_state(self, key, value):
        self.state[key] = value

    def clear_state(self, key):
        self.state.pop(key, None)


class ControllerTest(unittest.TestCase):
    def make(self, store=None, report_minutes=120):
        self.store = store or FakeStore()
        self.runs, self.reports = [], 0
        self.now = 0.0

        def run(cont):
            self.runs.append(cont())  # whether the window check says "go" at the start
            return {"audio": {"indexed": 3}}

        def build_report():
            self.reports += 1
            return {"audio": {"pending": 2}}

        return IngestionController(
            run, build_report, self.store, IndexingWindow("22:00", "05:00", "America/Santo_Domingo"),
            report_minutes=report_minutes, clock=lambda: self.now,
        )

    def test_nothing_runs_in_daytime_but_the_first_report_is_built(self):
        c = self.make()
        self.assertEqual(c.tick(DAY), "report")
        self.assertEqual(self.runs, [])
        self.assertEqual(self.store.state[REPORT], {"audio": {"pending": 2}})

    def test_report_is_periodic(self):
        c = self.make(report_minutes=60)
        c.tick(DAY)
        self.now = 30 * 60
        self.assertIsNone(c.tick(DAY))
        self.now = 61 * 60
        self.assertEqual(c.tick(DAY), "report")
        self.assertEqual(self.reports, 2)

    def test_report_on_request(self):
        c = self.make()
        c.tick(DAY)
        self.store.set_state(REPORT_REQUEST, {"by": "x"})
        self.assertEqual(c.tick(DAY), "report")
        self.assertNotIn(REPORT_REQUEST, self.store.state)

    def test_run_now_ignores_the_window_and_clears_the_request(self):
        c = self.make(FakeStore(**{RUN_REQUEST: {"mode": "now"}}))
        self.assertEqual(c.tick(DAY), "now")
        self.assertEqual(self.runs, [True])
        self.assertNotIn(RUN_REQUEST, self.store.state)
        self.assertFalse(self.store.state[RUN_STATUS]["running"])
        self.assertEqual(self.store.state[LAST_RUN]["mode"], "now")
        self.assertEqual(self.store.state[LAST_RUN]["results"], {"audio": {"indexed": 3}})
        self.assertEqual(self.reports, 1)  # a fresh report after the run

    def test_nightly_run_once_per_night(self):
        c = self.make()
        self.assertEqual(c.tick(NIGHT), "night")
        self.assertEqual(self.store.state[LAST_RUN]["mode"], "night")
        self.assertNotEqual(c.tick(NIGHT), "night")

    def test_a_failing_run_is_recorded_and_the_loop_survives(self):
        c = self.make(FakeStore(**{RUN_REQUEST: {"mode": "now"}}))
        c.run = mock.Mock(side_effect=RuntimeError("boom"))
        self.assertEqual(c.tick(DAY), "now")
        self.assertEqual(self.store.state[LAST_RUN]["results"], {"error": "boom"})

    def test_store_errors_do_not_stop_the_loop(self):
        c = self.make()
        self.store.get_state = mock.Mock(side_effect=ConnectionError("db down"))
        c.tick(DAY)  # must not raise


class ReportTest(unittest.TestCase):
    def test_compare_counts_pending_and_orphans(self):
        info = compare({"a", "b", "c"}, {"a", "z"})
        self.assertEqual((info["remote"], info["indexed"], info["pending"], info["orphans"]), (3, 1, 2, 1))
        self.assertEqual(info["pending_files"], ["b", "c"])


class FolderSelectionTest(unittest.TestCase):
    def syncer(self, allowed):
        s = SharePointSyncBase(Path("dl"), "state.json", "site", "lib", root_folder="3CX Recordings")
        s.folder_filter = ingestion_config.folder_filter(allowed)
        return s

    def test_no_configuration_means_everything(self):
        self.assertIsNone(ingestion_config.folder_filter({}))

    def test_unselected_and_new_folders_are_left_out(self):
        s = self.syncer({"1031": {"enabled": True}, "1032": {"enabled": False}})
        self.assertTrue(s.folder_filter("1031"))
        self.assertFalse(s.folder_filter("1032"))
        self.assertFalse(s.folder_filter("9999"))

    def test_excluded_paths_are_not_judged_as_deleted(self):
        s = self.syncer({"1031": {"enabled": True}})
        self.assertFalse(s.is_excluded("dl/3CX Recordings/1031/a.wav"))
        self.assertTrue(s.is_excluded("dl/3CX Recordings/1032/a.wav"))
        self.assertTrue(s.is_excluded("dl/3CX Recordings/loose.wav"))  # files in the root are skipped too
        self.assertFalse(s.is_excluded("other/place.wav"))

    def test_walk_only_enters_selected_folders(self):
        s = self.syncer({"1031": {"enabled": True}})
        tree = {
            "root": [
                {"name": "1031", "id": "a", "folder": {}},
                {"name": "1032", "id": "b", "folder": {}},
                {"name": "loose.wav", "id": "c", "file": {}},
            ],
            "a": [{"name": "x.wav", "id": "x", "file": {}}],
            "b": [{"name": "y.wav", "id": "y", "file": {}}],
        }
        s._get = lambda url: mock.Mock(json=lambda: {"value": tree[url.split("/items/")[1].split("/")[0]]})
        found = [i["name"] for i, _ in s.walk("site", "drive", "root", Path("dl/3CX Recordings"), top_level=True)]
        self.assertEqual(found, ["x.wav"])


class AdminEndpointsTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        self.store = FakeStore()
        patches = [
            mock.patch.object(ingestion_config, "get_state", self.store.get_state),
            mock.patch.object(ingestion_config, "set_state", self.store.set_state),
            mock.patch.object(ingestion_config, "clear_state", self.store.clear_state),
            mock.patch.object(ingestion_config, "load_folders", lambda: {"1031": {"enabled": True, "department": "COM"}}),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def headers(self, department):
        token = main.create_access_token({"sub": "a@b.c", "department": department, "position": "x"})
        return {"Authorization": f"Bearer {token}"}

    def test_department_check_ignores_accents_and_case(self):
        self.assertTrue(is_admin_department("Tecnología"))
        self.assertTrue(is_admin_department("tecnologia "))
        self.assertFalse(is_admin_department("Comercial"))
        self.assertFalse(is_admin_department(None))

    def test_other_departments_are_forbidden_on_every_endpoint(self):
        h = self.headers("Comercial")
        calls = [
            self.client.get("/admin/ingestion/config", headers=h),
            self.client.post("/admin/ingestion/folders", json={"folders": []}, headers=h),
            self.client.get("/admin/ingestion/report", headers=h),
            self.client.post("/admin/ingestion/report/refresh", headers=h),
            self.client.post("/admin/ingestion/run", json={"mode": "now"}, headers=h),
        ]
        self.assertEqual([r.status_code for r in calls], [403] * 5)
        self.assertNotIn(RUN_REQUEST, self.store.state)

    def test_requires_a_token(self):
        self.assertIn(self.client.get("/admin/ingestion/config").status_code, (401, 403))

    def test_config_merges_found_and_saved_folders(self):
        self.store.set_state(REPORT, {"audio_folders": ["1031", "1040", "999"]})
        r = self.client.get("/admin/ingestion/config", headers=self.headers("Tecnología"))
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual([f["name"] for f in body["folders"]], ["999", "1031", "1040"])
        by_name = {f["name"]: f for f in body["folders"]}
        self.assertTrue(by_name["1031"]["enabled"])
        self.assertEqual(by_name["1031"]["department"], "COM")
        self.assertFalse(by_name["1040"]["enabled"])
        self.assertTrue(body["configured"])
        self.assertEqual(body["window"]["start"], "22:00")
        self.assertIn("COM", [d["code"] for d in body["departments"]])

    def test_save_folders(self):
        saved = {}
        with mock.patch.object(ingestion_config, "save_folders", lambda items, user: saved.update(items=items, user=user) or ["1031"]):
            r = self.client.post(
                "/admin/ingestion/folders",
                json={"folders": [{"name": "1031", "enabled": True, "department": "HR"}]},
                headers=self.headers("Tecnología"),
            )
        self.assertEqual(r.json(), {"saved": 1, "relabeled": ["1031"]})
        self.assertEqual(saved["user"], "a@b.c")

    def test_save_rejects_unknown_department(self):
        with mock.patch.object(ingestion_config, "_table"), mock.patch.object(ingestion_config.global_supabase_client, "schema"):
            r = self.client.post(
                "/admin/ingestion/folders",
                json={"folders": [{"name": "1031", "enabled": True, "department": "NOPE"}]},
                headers=self.headers("Tecnología"),
            )
        self.assertEqual(r.status_code, 422)

    def test_run_now_then_night_cancels(self):
        h = self.headers("Tecnología")
        self.assertEqual(self.client.post("/admin/ingestion/run", json={"mode": "now"}, headers=h).status_code, 202)
        self.assertEqual(self.store.state[RUN_REQUEST]["mode"], "now")
        self.client.post("/admin/ingestion/run", json={"mode": "night"}, headers=h)
        self.assertNotIn(RUN_REQUEST, self.store.state)

    def test_run_now_conflicts_while_running(self):
        self.store.set_state(RUN_STATUS, {"running": True})
        r = self.client.post("/admin/ingestion/run", json={"mode": "now"}, headers=self.headers("Tecnología"))
        self.assertEqual(r.status_code, 409)

    def test_invalid_mode(self):
        r = self.client.post("/admin/ingestion/run", json={"mode": "later"}, headers=self.headers("Tecnología"))
        self.assertEqual(r.status_code, 422)

    def test_report_endpoint_and_refresh(self):
        h = self.headers("Tecnología")
        self.store.set_state(REPORT, {"audio": {"pending": 4}})
        body = self.client.get("/admin/ingestion/report", headers=h).json()
        self.assertEqual(body["report"], {"audio": {"pending": 4}})
        self.client.post("/admin/ingestion/report/refresh", headers=h)
        self.assertIn(REPORT_REQUEST, self.store.state)


if __name__ == "__main__":
    unittest.main()
