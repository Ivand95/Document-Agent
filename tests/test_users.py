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
from rbac_fixtures import FakeRBAC, role_id  # noqa: E402

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
    """The users admin API against the in-memory tables (FakeRBAC writes its users there too)."""

    def setUp(self):
        self.env(APP_ENV="test", JWT_SECRET_KEY=STRONG_SECRET)
        self.client = TestClient(main.app)
        self.rbac = FakeRBAC().install(self)
        self.set = self.patch(users, "set_status", return_value=users.User(id=OTHER, status="active", perm_version=2))
        self.rbac.add_user("Tecnología", "Analista", user_id=UID, name="Ana Admin", email="ana@coop.test")
        self.rbac.add_user("Marketing", "Analista", status="pending", user_id=OTHER, name="Pedro Pérez",
                           email="pedro@coop.test")

    def login(self, user_id=UID, sub="admin@coop.test"):
        token = security.create_access_token({"sub": sub, "department": "x", "position": "x", "user_id": user_id})
        return {"Authorization": f"Bearer {token}"}

    def headers(self, department="Tecnología", roles=(), **kwargs):
        """A new user of that department, signed in."""
        return self.login(self.rbac.add_user(department, "Analista", roles=roles, **kwargs))

    def manager(self, department="Marketing"):
        user_id = self.rbac.add_user(department, "Gerente", roles=["department_admin"], name="Gerente")
        return user_id, self.login(user_id, sub="gerente@coop.test")

    def listing(self, headers=None, query=""):
        r = self.client.get(f"/admin/users{query}", headers=headers or self.login())
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    # --- List and detail ----------------------------------------------------------------------------

    def test_list_with_counts_actions_and_protected_rows(self):
        super_id = self.rbac.add_user("Comercial", roles=["super_admin"], name="Root")
        body = self.listing()
        self.assertEqual((body["total"], body["counts"]), (3, {"pending": 1, "active": 2, "disabled": 0, "all": 3}))
        rows = {u["id"]: u for u in body["users"]}
        self.assertEqual((rows[OTHER]["actionable"], rows[OTHER]["actions"]), (True, ["approve", "disable"]))
        self.assertEqual((rows[UID]["actionable"], rows[UID]["protected"], rows[UID]["actions"]), (False, "self", []))
        self.assertEqual(rows[super_id]["protected"], "super_admin")
        self.assertFalse(body["scope"]["limited"])
        self.assertIn("Marketing", body["scope"]["departments"])
        page = self.listing(query="?limit=1&offset=1")
        self.assertEqual((len(page["users"]), page["total"]), (1, 3))
        self.assertEqual(self.client.get("/admin/users?status=root", headers=self.login()).status_code, 422)

    def test_search_and_department_filter(self):
        self.assertEqual([u["id"] for u in self.listing(query="?q=pérez")["users"]], [OTHER])
        self.assertEqual([u["id"] for u in self.listing(query="?q=PEDRO@coop")["users"]], [OTHER])
        body = self.listing(query="?q=pedro&status=active")
        self.assertEqual((body["total"], body["counts"]["pending"]), (0, 1))  # counts ignore the status filter
        self.assertEqual(self.listing(query="?q=*,)(%25_")["total"], 2)  # syntax characters are dropped
        self.assertEqual([u["id"] for u in self.listing(query="?department=tecnologia")["users"]], [UID])
        self.assertEqual(self.listing(query="?department=Nadie")["total"], 0)

    def test_detail(self):
        r = self.client.get(f"/admin/users/{OTHER}", headers=self.login())
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()["email"], r.json()["actions"], r.json()["created_by"]),
                         ("pedro@coop.test", ["approve", "disable"], "m365_login"))
        missing = "33333333-3333-3333-3333-333333333333"
        self.assertEqual(self.client.get(f"/admin/users/{missing}", headers=self.login()).status_code, 404)
        self.assertEqual(self.client.get("/admin/users/not-a-uuid", headers=self.login()).status_code, 422)

    # --- Department admins ----------------------------------------------------------------------------

    def test_department_admins_see_and_change_only_their_departments(self):
        manager_id, h = self.manager()
        other_manager = self.rbac.add_user("Marketing", roles=["department_admin"], status="disabled")
        sales = self.rbac.add_user("Comercial", name="Vendedor")
        body = self.listing(h)
        self.assertEqual(body["scope"], {"limited": True, "departments": ["Marketing"]})
        rows = {u["id"]: u for u in body["users"]}
        self.assertEqual(set(rows), {OTHER, manager_id, other_manager})  # no Tecnología, no Comercial
        self.assertEqual(rows[other_manager]["protected"], "admin")  # not even to re-enable a disabled one
        self.assertEqual(rows[manager_id]["protected"], "self")
        self.assertEqual(self.listing(h, "?department=Tecnología")["total"], 0)
        self.assertEqual(self.client.get(f"/admin/users/{UID}", headers=h).status_code, 404)
        self.assertEqual(self.client.post(f"/admin/users/{UID}/status", json={"status": "disabled"},
                                          headers=h).status_code, 404)
        self.assertEqual(self.client.post(f"/admin/users/{other_manager}/status", json={"status": "active"},
                                          headers=h).status_code, 403)
        self.set.assert_not_called()
        self.assertEqual(self.client.post(f"/admin/users/{OTHER}/status", json={"status": "active"},
                                          headers=h).status_code, 200)
        # An extra department widens the scope
        users.add_admin_department(manager_id, "Comercial", "test")
        body = self.listing(h)
        self.assertEqual(body["scope"]["departments"], ["Comercial", "Marketing"])
        self.assertIn(sales, {u["id"] for u in body["users"]})
        self.assertEqual([u["id"] for u in self.listing(h, "?department=comercial")["users"]], [sales])

    def test_an_admin_who_is_also_department_admin_is_not_limited(self):
        h = self.headers("Tecnología", roles=["department_admin"])  # admin through the Tecnología mapping
        self.assertFalse(self.listing(h)["scope"]["limited"])

    def test_admin_departments_endpoints(self):
        manager_id, manager_headers = self.manager()
        path = f"/admin/users/{manager_id}/admin-departments"
        self.assertEqual(self.client.get(path, headers=manager_headers).status_code, 403)  # needs the rbac area
        body = self.client.get(path, headers=self.login()).json()
        self.assertEqual(body, {"user_id": manager_id, "is_department_admin": True, "department": "Marketing",
                                "extra": []})
        self.rbac.add_user("Comercial")  # a department someone has signed in from
        r = self.client.post(path, json={"department": "comercial"}, headers=self.login())
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual([d["department"] for d in r.json()["extra"]], ["Comercial"])
        for department, status in (("Comercial", 409), ("marketing", 409), ("Nowhere", 422)):
            self.assertEqual(self.client.post(path, json={"department": department}, headers=self.login()).status_code,
                             status, department)
        self.assertEqual(self.client.post(f"/admin/users/{OTHER}/admin-departments", json={"department": "Comercial"},
                                          headers=self.login()).status_code, 409)  # not a department admin
        r = self.client.delete(f"{path}/COMERCIAL", headers=self.login())
        self.assertEqual((r.status_code, r.json()["extra"]), (200, []))
        self.assertEqual(self.client.delete(f"{path}/Comercial", headers=self.login()).status_code, 404)
        actions = [a[0] for a in self.rbac.audit]
        self.assertIn("user.admin_department_add", actions)
        self.assertIn("user.admin_department_remove", actions)

    # --- Roles ------------------------------------------------------------------------------------------

    def roles(self, user_id=OTHER, headers=None):
        r = self.client.get(f"/admin/users/{user_id}/roles", headers=headers or self.login())
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_roles_with_their_origin(self):
        body = self.roles()
        self.assertTrue(body["manage_all_roles"])
        held = {r["key"]: r for r in body["roles"]}
        self.assertEqual(held["dept_marketing"]["sources"], ["department:Marketing"])
        self.assertEqual(held["employee"]["sources"], ["everyone"])
        self.assertTrue(held["employee"]["automatic"])
        self.assertFalse(held["employee"]["removable"])
        assignable = {r["key"] for r in body["assignable"]}
        self.assertIn("admin", assignable)
        self.assertNotIn("super_admin", assignable)  # only a super admin gives it
        self.assertEqual(self.roles(UID)["assignable"], [])  # nobody changes their own roles

    def test_assign_with_expiry_and_remove(self):
        audio = role_id("audio_user")
        path = f"/admin/users/{OTHER}/roles"
        r = self.client.post(path, json={"role_id": audio, "expires_at": "2099-01-01T00:00:00+00:00"},
                             headers=self.login())
        self.assertEqual(r.status_code, 201, r.text)
        held = {x["key"]: x for x in r.json()["roles"]}
        self.assertEqual((held["audio_user"]["sources"], held["audio_user"]["removable"]), (["assigned"], True))
        self.assertTrue(held["audio_user"]["expires_at"].startswith("2099-01-01"))
        self.assertNotIn("audio_user", {x["key"] for x in r.json()["assignable"]})
        r = self.client.delete(f"{path}/{audio}", headers=self.login())
        self.assertEqual(r.status_code, 200, r.text)
        self.assertNotIn("audio_user", {x["key"] for x in r.json()["roles"]})
        self.assertEqual(self.client.delete(f"{path}/{audio}", headers=self.login()).status_code, 404)
        r = self.client.delete(f"{path}/{role_id('dept_marketing')}", headers=self.login())
        self.assertEqual(r.status_code, 409)
        self.assertIn("department:Marketing", r.json()["detail"])
        self.assertEqual(self.client.post(path, json={"role_id": role_id("super_admin")}, headers=self.login())
                         .status_code, 403)
        self.assertEqual(self.client.post(f"/admin/users/{UID}/roles", json={"role_id": audio},
                                          headers=self.login()).status_code, 400)
        self.assertEqual([a[0] for a in self.rbac.audit].count("role.assign"), 1)

    def test_department_admins_give_only_manager_assignable_roles(self):
        manager_id, h = self.manager()
        body = self.roles(headers=h)
        self.assertFalse(body["manage_all_roles"])
        self.assertEqual(body["roles"], [])  # dept_marketing, employee: not manager-assignable, not shown
        self.assertEqual([r["key"] for r in body["assignable"]], ["audio_user", "report_user"])
        path = f"/admin/users/{OTHER}/roles"
        self.assertEqual(self.client.post(path, json={"role_id": role_id("audio_user")}, headers=h).status_code, 201)
        for role in ("admin", "department_admin", "dept_marketing"):
            self.assertEqual(self.client.post(path, json={"role_id": role_id(role)}, headers=h).status_code, 404, role)
        self.assertEqual(self.client.get(f"/admin/users/{UID}/roles", headers=h).status_code, 404)  # out of scope
        other_manager = self.rbac.add_user("Marketing", roles=["department_admin"])
        self.assertEqual(self.client.post(f"/admin/users/{other_manager}/roles", json={"role_id": role_id("audio_user")},
                                          headers=h).status_code, 403)
        self.assertEqual(self.client.get("/admin/users/{}/roles".format(OTHER), headers=self.headers("Comercial"))
                         .status_code, 403)

    def test_roles_that_open_admin_areas_are_never_delegated(self):
        r = self.client.patch(f"/admin/rbac/roles/{role_id('admin')}", json={"manager_assignable": True},
                              headers=self.login())
        self.assertEqual(r.status_code, 409)
        # Marked by hand, then given an admin area later: still refused
        from rbac.model import Role, Rule, delegable
        from dataclasses import replace

        policy = self.rbac.policy
        rid = role_id("audio_user")
        self.assertTrue(delegable(policy, rid))
        risky = replace(policy, role_rules={**policy.role_rules, rid: policy.role_rules.get(rid, ()) + (
            Rule(id="x", subject_type="role", subject_id=rid, resource_type="admin_area", resource_id="users",
                 action="admin", effect="allow"),)})
        self.assertFalse(delegable(risky, rid))
        self.assertFalse(delegable(replace(policy, roles={**policy.roles, rid: Role(rid, "audio_user", "x")}), rid))

    # --- Status (as before) ---------------------------------------------------------------------------

    def test_status_changes_are_audited(self):
        self.client.post(f"/admin/users/{OTHER}/status", json={"status": "active"}, headers=self.login())
        self.assertEqual(self.rbac.audit[-1][:3], ("user.status", "user", OTHER))

    def test_only_a_super_admin_changes_a_super_admin(self):
        target = self.rbac.add_user("Marketing", roles=["super_admin"])
        r = self.client.post(f"/admin/users/{target}/status", json={"status": "disabled"}, headers=self.login())
        self.assertEqual(r.status_code, 403)
        self.set.assert_not_called()
        r = self.client.post(f"/admin/users/{target}/status", json={"status": "disabled"},
                             headers=self.headers("Marketing", roles=["super_admin"]))
        self.assertEqual(r.status_code, 200)

    def test_only_admins_can_list_or_change_users(self):
        h = self.headers("Comercial")
        self.assertEqual(self.client.get("/admin/users", headers=h).status_code, 403)
        self.assertEqual(self.client.get(f"/admin/users/{OTHER}", headers=h).status_code, 403)
        self.assertEqual(self.client.post(f"/admin/users/{OTHER}/status", json={"status": "active"}, headers=h).status_code, 403)
        self.assertEqual(self.client.get("/admin/users").status_code, 401)
        self.set.assert_not_called()

    def test_approve_a_user(self):
        r = self.client.post(f"/admin/users/{OTHER}/status", json={"status": "active"}, headers=self.login())
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"id": OTHER, "status": "active"})
        user_id, status, by = self.set.call_args.args
        self.assertEqual((str(user_id), status, by), (OTHER, "active", "admin@coop.test"))

    def test_admins_cannot_change_their_own_status(self):
        r = self.client.post(f"/admin/users/{UID}/status", json={"status": "disabled"}, headers=self.login())
        self.assertEqual(r.status_code, 400)
        self.set.assert_not_called()

    def test_bad_input_and_unknown_user(self):
        h = self.login()
        self.assertEqual(self.client.post("/admin/users/not-a-uuid/status", json={"status": "active"}, headers=h).status_code, 422)
        self.assertEqual(self.client.post(f"/admin/users/{OTHER}/status", json={"status": "root"}, headers=h).status_code, 422)
        missing = "33333333-3333-3333-3333-333333333333"
        self.assertEqual(self.client.post(f"/admin/users/{missing}/status", json={"status": "active"}, headers=h).status_code, 404)

    def test_there_is_no_way_to_create_users(self):
        h = self.login()
        self.assertEqual(self.client.post("/admin/users", json={"email": "x@y"}, headers=h).status_code, 405)
        writes = {
            path for path, ops in main.app.openapi()["paths"].items()
            if path.startswith("/admin/users") and set(ops) & {"post", "put", "patch"}
        }
        self.assertEqual(writes, {"/admin/users/{user_id}/status", "/admin/users/{user_id}/admin-departments",
                                  "/admin/users/{user_id}/roles"})


if __name__ == "__main__":
    unittest.main()
