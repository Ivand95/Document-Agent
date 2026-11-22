import os
import sys
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi.testclient import TestClient  # noqa: E402
from jose import jwt  # noqa: E402

import main  # noqa: E402
from api import auth as api_auth  # noqa: E402
from core import security  # noqa: E402
from integrations import m365  # noqa: E402
from models.user_profile import UserProfile  # noqa: E402
from users import service as users  # noqa: E402
from api.admin import users as admin_users  # noqa: E402
from rbac_fixtures import FakeRBAC  # noqa: E402

UID = "11111111-1111-1111-1111-111111111111"
OTHER = "22222222-2222-2222-2222-222222222222"
STRONG_SECRET = "x" * 40


def profile(**overrides):
    values = dict(id="oid-1", name="Ana", email="Ana@Coop.test", department="IT", position="Analista")
    return UserProfile(**{**values, **overrides})


class FakeQuery:
    """Records the PostgREST calls and returns canned rows."""

    def __init__(self, rows):
        self.rows, self.calls = rows, []

    def __getattr__(self, name):
        def record(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return self

        return record

    def execute(self):
        return mock.Mock(data=self.rows)


class EnvTestCase(unittest.TestCase):
    def env(self, **values):
        patcher = mock.patch.dict(os.environ, values)
        patcher.start()
        self.addCleanup(patcher.stop)

    def patch(self, target, name, **kwargs):
        patcher = mock.patch.object(target, name, **kwargs)
        started = patcher.start()
        self.addCleanup(patcher.stop)
        return started


class ServiceTest(EnvTestCase):
    def test_default_status_is_active_and_fails_closed_to_pending(self):
        os.environ.pop("USER_DEFAULT_STATUS", None)
        self.assertEqual(users.default_status(), "active")
        for value, expected in (("pending", "pending"), (" Active ", "active"), ("disabled", "pending"), ("x", "pending")):
            self.env(USER_DEFAULT_STATUS=value)
            self.assertEqual(users.default_status(), expected, value)

    def test_provision_sends_the_profile_and_reads_the_stored_status(self):
        self.env(USER_DEFAULT_STATUS="pending")
        query = FakeQuery([{"user_id": UID, "user_status": "pending", "user_perm_version": 3, "created": True}])
        db = self.patch(users, "_db")
        db.return_value.rpc.return_value = query

        user = users.provision(profile())

        name, params = db.return_value.rpc.call_args.args
        self.assertEqual(name, "upsert_m365_user")
        self.assertEqual(params["p_oid"], "oid-1")
        self.assertEqual(params["p_default_status"], "pending")
        self.assertEqual((params["p_department"], params["p_position"]), ("IT", "Analista"))
        self.assertEqual(user, users.User(id=UID, status="pending", perm_version=3, created=True))
        self.assertFalse(user.active)

    def test_provision_requires_the_object_id(self):
        with self.assertRaises(ValueError):
            users.provision(profile(id=""))

    def test_malformed_ids_never_reach_the_database(self):
        db = self.patch(users, "_db")
        self.assertIsNone(users.get_user("1 or 1=1"))
        self.assertIsNone(users.set_status("nope", "active", "admin"))
        db.assert_not_called()

    def test_set_status_rejects_unknown_statuses(self):
        with self.assertRaises(ValueError):
            users.set_status(UID, "root", "admin")

    def test_get_user_and_missing_user(self):
        db = self.patch(users, "_db")
        db.return_value.table.return_value = FakeQuery([{"id": UID, "status": "disabled", "perm_version": 2}])
        self.assertEqual(users.get_user(UID), users.User(id=UID, status="disabled", perm_version=2))
        db.return_value.table.return_value = FakeQuery([])
        self.assertIsNone(users.get_user(UID))


class ProfileTest(unittest.TestCase):
    def test_missing_department_or_position_stays_missing(self):
        p = UserProfile(id="1", name="A", email="a@b")
        self.assertIsNone(p.department)
        self.assertIsNone(p.position)  # used to become "IT"
        p = UserProfile(id="1", name="A", email="a@b", department="  ", position="")
        self.assertIsNone(p.department)
        self.assertIsNone(p.position)
        self.assertEqual(UserProfile(id="1", name="A", email="a@b", position=" GR - Ventas ").position, "GR - Ventas")


class TenantTest(EnvTestCase):
    def test_shared_tenants_are_refused_in_production_and_warned_otherwise(self):
        for tenant in (None, "", "common", "Organizations", "consumers"):
            with mock.patch.object(m365, "TENANT_ID", tenant):
                with self.assertRaisesRegex(RuntimeError, "OFFICE_365_TENANT_ID"):
                    m365.validate_tenant(production=True)
                with self.assertLogs("m365", level="WARNING"):
                    m365.validate_tenant(production=False)
        with mock.patch.object(m365, "TENANT_ID", "3f2b6a8c-0000-4000-8000-000000000000"):
            m365.validate_tenant(production=True)


class CallbackTest(EnvTestCase):
    def setUp(self):
        self.env(APP_ENV="test", JWT_SECRET_KEY=STRONG_SECRET)
        self.client = TestClient(main.app)
        self.patch(api_auth, "exchange_code_for_token", new=mock.AsyncMock(return_value={"access_token": "ms"}))
        self.get_profile = self.patch(api_auth, "get_user_profile", new=mock.AsyncMock(return_value=profile()))
        self.provision = self.patch(users, "provision", return_value=users.User(id=UID, status="active", perm_version=4))

    def callback(self):
        r = self.client.get("/login", headers={"Origin": "http://localhost:5173"})
        state = parse_qs(urlparse(r.json()["login_url"]).query)["state"][0]
        return self.client.get(f"/auth/callback?code=abc&state={state}").text

    def token_in(self, html):
        start = html.index('"access_token": "') + len('"access_token": "')
        return html[start:html.index('"', start)]

    def test_active_user_gets_a_token_with_user_id_and_perm_version(self):
        html = self.callback()
        self.provision.assert_called_once()
        claims = jwt.get_unverified_claims(self.token_in(html))
        self.assertEqual((claims["user_id"], claims["perm_version"]), (UID, 4))
        self.assertEqual((claims["department"], claims["position"]), ("IT", "Analista"))

    def test_pending_and_disabled_users_get_a_message_and_no_token(self):
        for status in ("pending", "disabled"):
            self.provision.return_value = users.User(id=UID, status=status, perm_version=1)
            html = self.callback()
            self.assertNotIn("access_token", html)
            self.assertIn(f'"status": "{status}"', html)
            self.assertIn(api_auth.STATUS_MESSAGES[status].split()[0], html)

    def test_profile_without_department_or_position_is_registered_but_gets_no_token(self):
        for missing in ({"department": None}, {"position": None}):
            self.get_profile.return_value = profile(**missing)
            self.provision.reset_mock()
            html = self.callback()
            self.provision.assert_called_once()  # the admin still sees the user
            self.assertNotIn("access_token", html)
            self.assertIn("departamento o cargo", html)

    def test_registration_failure_gives_the_generic_error(self):
        self.provision.side_effect = RuntimeError("db down: secret-detail")
        html = self.callback()
        self.assertNotIn("access_token", html)
        self.assertNotIn("secret-detail", html)
        self.assertIn(api_auth.LOGIN_ERROR.split()[0], html)


class RefreshTest(EnvTestCase):
    def setUp(self):
        self.env(APP_ENV="test", JWT_SECRET_KEY=STRONG_SECRET)
        self.client = TestClient(main.app)
        self.get_user = self.patch(users, "get_user", return_value=users.User(id=UID, status="active", perm_version=7))

    def refresh(self, **claims):
        base = {"sub": "a@b", "department": "IT", "position": "x", "user_id": UID, "perm_version": 1}
        token = security.create_access_token({**base, **claims})
        return self.client.post("/auth/refresh", headers={"Authorization": f"Bearer {token}"})

    def test_active_user_gets_the_current_perm_version(self):
        r = self.refresh()
        self.assertEqual(r.status_code, 200)
        claims = security.decode_token(r.json()["access_token"])
        self.assertEqual((claims["user_id"], claims["perm_version"]), (UID, 7))
        self.get_user.assert_called_once_with(UID)

    def test_inactive_or_unknown_users_must_log_in_again(self):
        for user in (users.User(id=UID, status="disabled", perm_version=2),
                     users.User(id=UID, status="pending", perm_version=2), None):
            self.get_user.return_value = user
            self.assertEqual(self.refresh().status_code, 401)

    def test_tokens_from_before_registration_must_log_in_again(self):
        self.assertEqual(self.refresh(user_id=None).status_code, 401)
        token = security.create_access_token({"sub": "a@b", "department": "IT", "position": "x"})
        r = self.client.post("/auth/refresh", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(r.status_code, 401)

    def test_database_errors_are_not_reported_as_logouts(self):
        self.get_user.side_effect = RuntimeError("db down")
        r = self.refresh()
        self.assertEqual(r.status_code, 503)
        self.assertNotIn("db down", r.text)

    def test_refresh_token_keeps_the_user_claims(self):
        claims = security.decode_token(security.create_access_token({"sub": "a", "user_id": UID, "perm_version": 1}))
        renewed = security.decode_token(security.refresh_token(claims, perm_version=5))
        self.assertEqual((renewed["user_id"], renewed["perm_version"]), (UID, 5))


class AdminUsersTest(EnvTestCase):
    def setUp(self):
        self.env(APP_ENV="test", JWT_SECRET_KEY=STRONG_SECRET)
        self.client = TestClient(main.app)
        self.rbac = FakeRBAC().install(self)
        self.list = self.patch(users, "list_users", return_value=[{"id": OTHER, "status": "pending"}])
        self.set = self.patch(users, "set_status", return_value=users.User(id=OTHER, status="active", perm_version=2))
        self.target_roles = self.patch(admin_users, "roles_held_by_hand", return_value=set())

    def headers(self, department="Tecnología", user_id=UID, roles=()):
        self.rbac.add_user(department, "x", roles=roles, user_id=user_id)
        token = security.create_access_token(
            {"sub": "admin@coop.test", "department": department, "position": "x", "user_id": user_id}
        )
        return {"Authorization": f"Bearer {token}"}

    def test_status_changes_are_audited(self):
        self.client.post(f"/admin/users/{OTHER}/status", json={"status": "active"}, headers=self.headers())
        self.assertEqual(self.rbac.audit[-1][:3], ("user.status", "user", OTHER))

    def test_only_a_super_admin_changes_a_super_admin(self):
        self.target_roles.return_value = {"super_admin"}
        r = self.client.post(f"/admin/users/{OTHER}/status", json={"status": "disabled"}, headers=self.headers())
        self.assertEqual(r.status_code, 403)
        self.set.assert_not_called()
        r = self.client.post(f"/admin/users/{OTHER}/status", json={"status": "disabled"},
                             headers=self.headers(department="Marketing", roles=["super_admin"]))
        self.assertEqual(r.status_code, 200)

    def test_only_admins_can_list_or_change_users(self):
        h = self.headers(department="Comercial")
        self.assertEqual(self.client.get("/admin/users", headers=h).status_code, 403)
        self.assertEqual(self.client.post(f"/admin/users/{OTHER}/status", json={"status": "active"}, headers=h).status_code, 403)
        self.assertEqual(self.client.get("/admin/users").status_code, 401)
        self.list.assert_not_called()
        self.set.assert_not_called()

    def test_list_users_with_status_filter(self):
        r = self.client.get("/admin/users?status=pending&limit=10&offset=20", headers=self.headers())
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["users"], [{"id": OTHER, "status": "pending"}])
        self.list.assert_called_once_with("pending", 10, 20)
        self.assertEqual(self.client.get("/admin/users?status=root", headers=self.headers()).status_code, 422)

    def test_approve_a_user(self):
        r = self.client.post(f"/admin/users/{OTHER}/status", json={"status": "active"}, headers=self.headers())
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"id": OTHER, "status": "active"})
        user_id, status, by = self.set.call_args.args
        self.assertEqual((str(user_id), status, by), (OTHER, "active", "admin@coop.test"))

    def test_admins_cannot_change_their_own_status(self):
        r = self.client.post(f"/admin/users/{UID}/status", json={"status": "disabled"}, headers=self.headers())
        self.assertEqual(r.status_code, 400)
        self.set.assert_not_called()

    def test_bad_input_and_unknown_user(self):
        h = self.headers()
        self.assertEqual(self.client.post("/admin/users/not-a-uuid/status", json={"status": "active"}, headers=h).status_code, 422)
        self.assertEqual(self.client.post(f"/admin/users/{OTHER}/status", json={"status": "root"}, headers=h).status_code, 422)
        self.set.return_value = None
        self.assertEqual(self.client.post(f"/admin/users/{OTHER}/status", json={"status": "active"}, headers=h).status_code, 404)

    def test_there_is_no_way_to_create_users(self):
        h = self.headers()
        self.assertEqual(self.client.post("/admin/users", json={"email": "x@y"}, headers=h).status_code, 405)
        writes = {
            path for path, ops in main.app.openapi()["paths"].items()
            if path.startswith("/admin/users") and set(ops) & {"post", "put", "patch"}
        }
        self.assertEqual(writes, {"/admin/users/{user_id}/status"})


if __name__ == "__main__":
    unittest.main()
