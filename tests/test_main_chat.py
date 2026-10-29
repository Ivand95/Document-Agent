import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

from fastapi.testclient import TestClient  # noqa: E402
from starlette.websockets import WebSocketDisconnect  # noqa: E402

import main  # noqa: E402


class FakeGraph:
    def __init__(self):
        self.calls = []

    async def ainvoke(self, inputs, config=None):
        self.calls.append((inputs, config))
        return {"answer": {"answer": "ok"}}


class MainChatTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        self.graph = FakeGraph()
        for name in ("get_app_graph", "get_app_audio_graph"):
            patcher = mock.patch.object(main, name, return_value=self.graph)
            patcher.start()
            self.addCleanup(patcher.stop)

    def token(self, **claims):
        return main.create_access_token({"department": "Tecnología", "position": "Analista", **claims})

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
        self.assertTrue(config["configurable"]["thread_id"])  # the checkpointer requires one

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
        return self.client.websocket_connect(f"/ws/chat/audio?token={token}")

    def env(self, **values):
        patcher = mock.patch.dict(os.environ, values)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_audio_ws_rejects_invalid_token(self):
        with self.assertRaises(WebSocketDisconnect):
            with self.connect_audio("nope"):
                pass

    def test_audio_ws_rejects_token_without_position(self):
        token = main.create_access_token({"department": "Auditoria"})
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


if __name__ == "__main__":
    unittest.main()
