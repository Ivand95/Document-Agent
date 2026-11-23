import os
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

from fastapi.testclient import TestClient  # noqa: E402

import indexing_runner  # noqa: E402
import main  # noqa: E402


class IndexingEndpointsTest(unittest.TestCase):
    def setUp(self):
        indexing_runner._state.update(
            running=False, params=None, started_at=None, finished_at=None, result=None
        )
        self.client = TestClient(main.app)
        token = main.create_access_token({"department": "IT", "position": "dev"})
        self.auth = {"Authorization": f"Bearer {token}"}

    def env(self, value):
        patcher = mock.patch.dict(os.environ, {"APP_ENV": value} if value else {}, clear=False)
        if not value:
            os.environ.pop("APP_ENV", None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def wait_until_done(self):
        for _ in range(100):
            if not indexing_runner.indexing_status()["running"]:
                return
            time.sleep(0.02)
        self.fail("run did not finish")

    def test_blocked_in_production_even_with_valid_token(self):
        for value in (None, "production", "staging", ""):
            self.env(value)
            with mock.patch.object(indexing_runner, "run_indexing") as run:
                r = self.client.post("/admin/indexing/run", json={}, headers=self.auth)
                self.assertEqual(r.status_code, 403, value)
                self.assertEqual(self.client.get("/admin/indexing/status", headers=self.auth).status_code, 403)
                run.assert_not_called()

    def test_requires_token_in_development(self):
        self.env("development")
        self.assertIn(self.client.post("/admin/indexing/run", json={}).status_code, (401, 403))
        bad = {"Authorization": "Bearer nope"}
        self.assertEqual(self.client.post("/admin/indexing/run", json={}, headers=bad).status_code, 401)

    def test_run_passes_params_and_reports_result(self):
        self.env("development")
        with mock.patch.object(indexing_runner, "run_indexing", return_value={"audio": {"indexed": 3}}) as run:
            r = self.client.post(
                "/admin/indexing/run",
                json={"target": "audio", "full": True, "limit": 5, "audio_folder": "3CX Recordings/recordings/1055"},
                headers=self.auth,
            )
            self.assertEqual(r.status_code, 202)
            self.wait_until_done()
            run.assert_called_once_with(
                target="audio",
                force=True,
                limit=5,
                docs_folder=None,
                audio_folder="3CX Recordings/recordings/1055",
            )
        status = self.client.get("/admin/indexing/status", headers=self.auth).json()
        self.assertFalse(status["running"])
        self.assertEqual(status["result"], {"audio": {"indexed": 3}})

    def test_second_run_while_running_is_rejected(self):
        self.env("development")
        release = threading.Event()
        with mock.patch.object(indexing_runner, "run_indexing", side_effect=lambda **_: release.wait(5) and {}):
            self.assertEqual(self.client.post("/admin/indexing/run", json={}, headers=self.auth).status_code, 202)
            self.assertEqual(self.client.post("/admin/indexing/run", json={}, headers=self.auth).status_code, 409)
            self.assertTrue(self.client.get("/admin/indexing/status", headers=self.auth).json()["running"])
            release.set()
            self.wait_until_done()

    def test_validation(self):
        self.env("development")
        for body in ({"limit": 0}, {"target": "everything"}):
            r = self.client.post("/admin/indexing/run", json=body, headers=self.auth)
            self.assertEqual(r.status_code, 422, body)

    def test_failure_is_reported_not_raised(self):
        self.env("development")
        with mock.patch.object(indexing_runner, "run_indexing", side_effect=RuntimeError("boom")):
            self.client.post("/admin/indexing/run", json={}, headers=self.auth)
            self.wait_until_done()
        status = self.client.get("/admin/indexing/status", headers=self.auth).json()
        self.assertEqual(status["result"], {"error": "boom"})


if __name__ == "__main__":
    unittest.main()
