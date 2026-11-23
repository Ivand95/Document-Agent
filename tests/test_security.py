import json
import os
import sys
import time
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

from fastapi.testclient import TestClient  # noqa: E402
from jose import JWTError, jwt  # noqa: E402
from starlette.websockets import WebSocketDisconnect  # noqa: E402

import main  # noqa: E402
import security  # noqa: E402
from models.user_profile import UserProfile  # noqa: E402

FRONT = "https://front.example.com"
STRONG_SECRET = "x" * 40


class EnvTestCase(unittest.TestCase):
    def env(self, **values):
        patcher = mock.patch.dict(os.environ, values)
        patcher.start()
        self.addCleanup(patcher.stop)

    def unset(self, *names):
        for name in names:
            patcher = mock.patch.dict(os.environ)
            patcher.start()
            os.environ.pop(name, None)
            self.addCleanup(patcher.stop)


class SettingsTest(EnvTestCase):
    def test_production_requires_a_strong_jwt_secret(self):
        self.env(APP_ENV="production", FRONTEND_URL=FRONT)
        self.unset("JWT_SECRET_KEY")
        with self.assertRaisesRegex(RuntimeError, "JWT_SECRET_KEY"):
            security.validate_settings()
        self.env(JWT_SECRET_KEY="change-me-in-production")
        with self.assertRaisesRegex(RuntimeError, "JWT_SECRET_KEY"):
            security.validate_settings()
        self.env(JWT_SECRET_KEY="short")
        with self.assertRaisesRegex(RuntimeError, "JWT_SECRET_KEY"):
            security.validate_settings()

    def test_production_requires_explicit_frontend_origins(self):
        self.env(APP_ENV="production", JWT_SECRET_KEY=STRONG_SECRET)
        for value in ("", "*", f"{FRONT},*"):
            self.env(FRONTEND_URL=value)
            with self.assertRaisesRegex(RuntimeError, "FRONTEND_URL"):
                security.validate_settings()

    def test_unset_app_env_counts_as_production(self):
        self.unset("APP_ENV")
        self.assertTrue(security.is_production())

    def test_valid_production_settings_pass(self):
        self.env(APP_ENV="production", JWT_SECRET_KEY=STRONG_SECRET, FRONTEND_URL=f"{FRONT}/, https://b.example.com")
        security.validate_settings()
        self.assertEqual(security.frontend_origins(), [FRONT, "https://b.example.com"])

    def test_development_does_not_require_anything(self):
        self.env(APP_ENV="development")
        self.unset("JWT_SECRET_KEY", "FRONTEND_URL")
        security.validate_settings()

    def test_startup_fails_closed_in_production(self):
        self.env(APP_ENV="production", FRONTEND_URL="")
        with self.assertRaises(RuntimeError):
            with TestClient(main.app):
                pass

    def test_origin_check(self):
        self.env(APP_ENV="production", FRONTEND_URL=FRONT)
        self.assertTrue(security.origin_allowed(FRONT))
        self.assertFalse(security.origin_allowed("https://evil.example.com"))
        self.assertFalse(security.origin_allowed(None))
        self.env(APP_ENV="development", FRONTEND_URL="")
        self.assertTrue(security.origin_allowed("http://localhost:5173"))


class TokenTest(EnvTestCase):
    def setUp(self):
        self.env(JWT_SECRET_KEY=STRONG_SECRET)

    def test_token_has_issuer_audience_and_unique_id(self):
        a = security.decode_token(security.create_access_token({"department": "IT"}))
        b = security.decode_token(security.create_access_token({"department": "IT"}))
        self.assertEqual((a["iss"], a["aud"]), (security.ISSUER, security.AUDIENCE))
        self.assertNotEqual(a["jti"], b["jti"])
        self.assertLessEqual(a["exp"] - a["iat"], security.ACCESS_TOKEN_MINUTES * 60)

    def test_wrong_audience_or_secret_is_rejected(self):
        now = int(time.time())
        claims = {"iss": security.ISSUER, "exp": now + 60, "jti": "1", "department": "IT"}
        no_aud = jwt.encode(claims, STRONG_SECRET, algorithm="HS256")
        with self.assertRaises(JWTError):
            security.decode_token(no_aud)  # tokens from before this change expire at deploy
        good = security.create_access_token({"department": "IT"})
        self.env(JWT_SECRET_KEY="y" * 40)
        with self.assertRaises(JWTError):
            security.decode_token(good)

    def test_expired_token_is_rejected(self):
        now = int(time.time())
        claims = {"iss": security.ISSUER, "aud": security.AUDIENCE, "jti": "1", "exp": now - 10, "department": "IT"}
        with self.assertRaises(JWTError):
            security.decode_token(jwt.encode(claims, STRONG_SECRET, algorithm="HS256"))

    def test_revoked_token_is_rejected(self):
        claims = security.decode_token(security.create_access_token({"department": "IT"}))
        security.revoke_token(claims)
        token_again = jwt.encode(claims, STRONG_SECRET, algorithm="HS256")
        with self.assertRaisesRegex(JWTError, "revoked"):
            security.decode_token(token_again)

    def test_refresh_keeps_the_session_start_and_stops_at_the_absolute_limit(self):
        start = int(time.time()) - 3600
        claims = security.decode_token(
            security.create_access_token({"sub": "a@b", "department": "IT", "position": "x"}, session_start=start)
        )
        renewed = security.decode_token(security.refresh_token(claims))
        self.assertEqual(renewed["session_start"], start)
        self.assertEqual((renewed["sub"], renewed["department"]), ("a@b", "IT"))

        old_start = int(time.time()) - (security.SESSION_MAX_HOURS * 3600 + 10)
        with self.assertRaises(JWTError):
            security.refresh_token({"session_start": old_start, "department": "IT"})

    def test_token_never_outlives_the_session_limit(self):
        start = int(time.time()) - (security.SESSION_MAX_HOURS * 3600 - 60)
        claims = security.decode_token(security.create_access_token({"department": "IT"}, session_start=start))
        self.assertLessEqual(claims["exp"], start + security.SESSION_MAX_HOURS * 3600)


class LoginFlowTest(EnvTestCase):
    def setUp(self):
        self.env(APP_ENV="test", JWT_SECRET_KEY=STRONG_SECRET)
        self.unset("FRONTEND_URL")
        self.client = TestClient(main.app)

    def assertLoginFailed(self, html):
        # The message is JSON-escaped inside the page ("sesi\\u00f3n"), so check the shape, not the text
        self.assertIn('"error"', html)
        self.assertNotIn("access_token", html)

    def start_login(self, origin="http://localhost:5173"):
        r = self.client.get("/login", headers={"Origin": origin})
        self.assertEqual(r.status_code, 200)
        return parse_qs(urlparse(r.json()["login_url"]).query)

    def test_login_url_carries_state_and_pkce(self):
        q = self.start_login()
        self.assertEqual(q["code_challenge_method"], ["S256"])
        self.assertTrue(q["state"][0] and q["code_challenge"][0])
        self.assertNotEqual(self.start_login()["state"], q["state"])

    def test_login_rejects_unknown_origin_in_production(self):
        self.env(APP_ENV="production", FRONTEND_URL=FRONT)
        self.assertEqual(self.client.get("/login", headers={"Origin": "https://evil.example.com"}).status_code, 403)
        self.assertEqual(self.client.get("/login").status_code, 403)
        self.assertEqual(self.client.get("/login", headers={"Origin": FRONT}).status_code, 200)

    def test_callback_without_valid_state_gives_a_generic_error(self):
        for params in ("code=abc", "code=abc&state=forged"):
            html = self.client.get(f"/auth/callback?{params}").text
            self.assertLoginFailed(html)

    def test_callback_success_posts_only_to_the_opener_origin_and_state_is_single_use(self):
        state = self.start_login(origin="https://front.example.com")["state"][0]
        profile = UserProfile(id="1", name='Ana </script><b>', email="ana@coop.test", department="IT", position="Analista")
        with mock.patch.object(main, "exchange_code_for_token", mock.AsyncMock(return_value={"access_token": "ms"})) as ex, \
             mock.patch.object(main, "get_user_profile", mock.AsyncMock(return_value=profile)):
            html = self.client.get(f"/auth/callback?code=abc&state={state}").text
            self.assertEqual(ex.call_args.args[0], "abc")
            self.assertTrue(ex.call_args.args[1])  # PKCE verifier
            self.assertIn('"https://front.example.com"', html)
            self.assertNotIn("</script><b>", html)  # escaped inside the script block
            self.assertIn("access_token", html)

            self.assertLoginFailed(self.client.get(f"/auth/callback?code=abc&state={state}").text)

    def test_callback_does_not_leak_exception_details(self):
        state = self.start_login()["state"][0]
        with mock.patch.object(main, "exchange_code_for_token", mock.AsyncMock(side_effect=Exception("secret-detail"))):
            html = self.client.get(f"/auth/callback?code=abc&state={state}").text
        self.assertNotIn("secret-detail", html)
        self.assertLoginFailed(html)


class SessionEndpointsTest(EnvTestCase):
    def setUp(self):
        self.env(APP_ENV="test", JWT_SECRET_KEY=STRONG_SECRET)
        self.client = TestClient(main.app)
        self.token = security.create_access_token({"sub": "a@b", "department": "IT", "position": "x"})
        self.auth = {"Authorization": f"Bearer {self.token}"}

    def test_refresh_returns_a_working_new_token(self):
        new = self.client.post("/auth/refresh", headers=self.auth).json()["access_token"]
        self.assertNotEqual(new, self.token)
        r = self.client.post("/auth/refresh", headers={"Authorization": f"Bearer {new}"})
        self.assertEqual(r.status_code, 200)

    def test_logout_invalidates_the_token(self):
        self.assertEqual(self.client.post("/auth/logout", headers=self.auth).status_code, 200)
        self.assertEqual(self.client.post("/auth/refresh", headers=self.auth).status_code, 401)

    def test_chat_rejects_oversized_or_empty_messages(self):
        big = "a" * (security.MAX_MESSAGE_CHARS + 1)
        for message in (big, ""):
            r = self.client.post("/chat", json={"message": message}, headers=self.auth)
            self.assertEqual(r.status_code, 422)


class FakeGraph:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    async def ainvoke(self, inputs, config=None):
        if self.fail:
            raise RuntimeError("db password is hunter2")
        self.calls.append(inputs)
        return {"answer": {"answer": "ok"}}


class WebSocketSecurityTest(EnvTestCase):
    def setUp(self):
        self.env(APP_ENV="test", JWT_SECRET_KEY=STRONG_SECRET)
        self.unset("FRONTEND_URL")
        self.client = TestClient(main.app)
        self.graph = FakeGraph()
        patcher = mock.patch.object(main, "get_app_graph", return_value=self.graph)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.claims = {"sub": "a@b", "department": "IT", "position": "Analista"}

    def connect(self, token, **kwargs):
        return self.client.websocket_connect(f"/ws/chat?token={token}", **kwargs)

    def test_rejects_foreign_origin_in_production(self):
        self.env(APP_ENV="production", FRONTEND_URL=FRONT)
        token = security.create_access_token(self.claims)
        with self.assertRaises(WebSocketDisconnect):
            with self.connect(token, headers={"origin": "https://evil.example.com"}):
                pass
        with self.assertRaises(WebSocketDisconnect):
            with self.connect(token):  # no Origin header
                pass
        with self.connect(token, headers={"origin": FRONT}) as ws:
            ws.send_json({"message": "hola"})
            self.assertEqual(ws.receive_json()["type"], "answer")

    def test_long_messages_are_refused_without_reaching_the_agent(self):
        with self.connect(security.create_access_token(self.claims)) as ws:
            ws.send_json({"message": "a" * (security.MAX_MESSAGE_CHARS + 1)})
            self.assertEqual(ws.receive_json()["type"], "error")
            ws.send_text("b" * (main.MAX_FRAME_CHARS + 1))
            self.assertEqual(ws.receive_json()["type"], "error")
        self.assertEqual(self.graph.calls, [])

    def test_revoked_token_ends_the_conversation(self):
        token = security.create_access_token(self.claims)
        with self.connect(token) as ws:
            ws.send_json({"message": "hola"})
            self.assertEqual(ws.receive_json()["type"], "answer")
            security.revoke_token(security.decode_token(token))
            ws.send_json({"message": "otra"})
            self.assertEqual(ws.receive_json()["type"], "error")
            with self.assertRaises(WebSocketDisconnect):
                ws.receive_json()
        self.assertEqual(len(self.graph.calls), 1)

    def test_token_can_be_renewed_without_reconnecting(self):
        token = security.create_access_token(self.claims)
        with self.connect(token) as ws:
            security.revoke_token(security.decode_token(token))
            renewed = security.create_access_token(self.claims)
            ws.send_json({"type": "auth", "token": renewed})
            ws.send_json({"message": "hola"})
            self.assertEqual(ws.receive_json()["type"], "answer")

    def test_renewal_for_another_user_is_refused(self):
        with self.connect(security.create_access_token(self.claims)) as ws:
            other = security.create_access_token({**self.claims, "sub": "someone@else"})
            ws.send_json({"type": "auth", "token": other})
            with self.assertRaises(WebSocketDisconnect):
                ws.receive_json()

    def test_agent_errors_are_not_leaked_to_the_client(self):
        self.graph.fail = True
        with self.connect(security.create_access_token(self.claims)) as ws:
            ws.send_json({"message": "hola"})
            payload = ws.receive_json()
        self.assertEqual(payload["type"], "error")
        self.assertNotIn("hunter2", json.dumps(payload))
        self.assertIn("Código", payload["content"])


if __name__ == "__main__":
    unittest.main()
