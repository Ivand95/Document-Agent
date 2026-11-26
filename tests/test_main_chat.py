import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi.testclient import TestClient  # noqa: E402
from starlette.websockets import WebSocketDisconnect  # noqa: E402

import main  # noqa: E402
from api import chat  # noqa: E402
from core import security  # noqa: E402
from rbac_fixtures import FakeRBAC, rule, ws_connect  # noqa: E402


class FakeGraph:
    def __init__(self):
        self.calls = []

    async def ainvoke(self, inputs, config=None):
        self.calls.append((inputs, config))
        return {"answer": {"answer": "ok"}}


class MainChatTest(unittest.TestCase):
    def setUp(self):
        # Development mode: no FRONTEND_URL is configured in tests (production rules live in test_security)
        patcher = mock.patch.dict(os.environ, {"APP_ENV": "test"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(main.app)
        self.rbac = FakeRBAC().install(self)
        self.graph = FakeGraph()
        for name in ("get_app_graph", "get_app_audio_graph"):
            patcher = mock.patch.object(chat, name, return_value=self.graph)
            patcher.start()
            self.addCleanup(patcher.stop)

    def token(self, department="Tecnología", position="Analista", **kwargs):
        return self.rbac.token(department, position, **kwargs)

    def test_startup_prepares_the_database(self):
        with mock.patch.object(main, "ensure_database") as ensure:
            with TestClient(main.app) as client:  # `with` runs the lifespan
                self.assertEqual(client.get("/health").json(), {"status": "ok"})
        ensure.assert_called_once()

    def test_chat_endpoint_passes_messages_position_and_a_thread_id(self):
        r = self.client.post(
            "/chat", json={"message": "hola"}, headers={"Authorization": f"Bearer {self.token()}"}
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["response"], {"answer": "ok"})

        inputs, config = self.graph.calls[0]
        self.assertEqual(inputs["messages"][0].content, "hola")
        self.assertEqual((inputs["user_department"], inputs["position"]), ("Tecnología", "Analista"))
        self.assertEqual(inputs["allowed_categories"], ["IT", "OTROS"])  # from RBAC
        self.assertTrue(config["configurable"]["thread_id"])  # the checkpointer requires one

    def test_chat_needs_the_documents_agent(self):
        token = self.token(rules=[rule("agent", "documents", effect="deny")])
        r = self.client.post("/chat", json={"message": "hola"}, headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.graph.calls, [])

    def test_chat_refuses_old_tokens_and_inactive_users(self):
        old = security.create_access_token({"department": "Tecnología", "position": "Analista"})  # no user_id
        disabled = self.token(status="disabled")
        for token in (old, disabled):
            r = self.client.post("/chat", json={"message": "hola"}, headers={"Authorization": f"Bearer {token}"})
            self.assertEqual(r.status_code, 401)
        self.assertEqual(self.graph.calls, [])

    def test_chat_fails_closed_when_permissions_cannot_be_read(self):
        token = self.token()
        self.rbac.fail = True
        from rbac import service
        service.invalidate()
        r = self.client.post("/chat", json={"message": "hola"}, headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(r.status_code, 503)
        self.assertEqual(self.graph.calls, [])

    def test_chat_threads_are_not_shared_between_requests(self):
        headers = {"Authorization": f"Bearer {self.token()}"}
        self.client.post("/chat", json={"message": "a"}, headers=headers)
        self.client.post("/chat", json={"message": "b"}, headers=headers)
        ids = {c[1]["configurable"]["thread_id"] for c in self.graph.calls}
        self.assertEqual(len(ids), 2)

    def test_chat_requires_a_valid_token(self):
        r = self.client.post("/chat", json={"message": "hola"}, headers={"Authorization": "Bearer nope"})
        self.assertEqual(r.status_code, 401)

    def connect_audio(self, token):
        return ws_connect(self.client, "/ws/chat/audio", token)

    def env(self, **values):
        patcher = mock.patch.dict(os.environ, values)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_audio_ws_rejects_invalid_token(self):
        with self.assertRaises(WebSocketDisconnect):
            with self.connect_audio("nope"):
                pass

    def test_audio_ws_rejects_token_without_position(self):
        token = security.create_access_token({"department": "Auditoria", "user_id": self.rbac.add_user("Auditoria")})
        with self.assertRaises(WebSocketDisconnect):
            with self.connect_audio(token):
                pass

    def test_audio_ws_rejects_unauthorized_department(self):
        self.env(AUDIO_ALLOWED_DEPARTMENTS="Auditoria")
        with self.assertRaises(WebSocketDisconnect) as ctx:
            with self.connect_audio(self.token()):
                pass
        self.assertEqual(ctx.exception.code, 1008)
        self.assertEqual(self.graph.calls, [])

    def test_audio_ws_allows_managers_and_allowed_departments(self):
        self.env(AUDIO_ALLOWED_DEPARTMENTS="Auditoria")
        for token in (self.token(position="GR - Director"), self.token(department="Auditoria")):
            with self.connect_audio(token) as ws:
                ws.send_json({"message": "resumen de la ext 1055"})
                payload = ws.receive_json()
            self.assertEqual(payload["type"], "answer")
            self.assertEqual(payload["content"], {"answer": "ok"})

        inputs, _ = self.graph.calls[-1]
        self.assertIn("position", inputs)
        self.assertNotIn("allowed_categories", inputs)  # the calls agent doesn't search documents

    def test_audio_ws_allows_an_assigned_role(self):
        with self.connect_audio(self.token(department="Marketing", roles=["audio_user"])) as ws:
            ws.send_json({"message": "hola"})
            self.assertEqual(ws.receive_json()["type"], "answer")

    def test_access_is_checked_again_for_every_message(self):
        token = self.token(position="GR - Director")
        user_id = security.decode_token(token)["user_id"]
        with self.connect_audio(token) as ws:
            ws.send_json({"message": "uno"})
            self.assertEqual(ws.receive_json()["type"], "answer")
            # An admin disables the user (this server clears its cache at once)
            self.rbac.add_user("Tecnología", "GR - Director", status="disabled", user_id=user_id)
            ws.send_json({"message": "dos"})
            self.assertEqual(ws.receive_json()["type"], "error")
            with self.assertRaises(WebSocketDisconnect):
                ws.receive_json()
        self.assertEqual(len(self.graph.calls), 1)

    def test_documents_ws_uses_rbac_categories(self):
        token = self.token(department="Marketing", rules=[rule("doc_category", "FI")])
        with ws_connect(self.client, "/ws/chat", token) as ws:
            ws.send_json({"message": "hola"})
            ws.receive_json()
        inputs, _ = self.graph.calls[-1]
        self.assertEqual(sorted(inputs["allowed_categories"]), ["FI", "MKT", "OTROS"])


if __name__ == "__main__":
    unittest.main()
