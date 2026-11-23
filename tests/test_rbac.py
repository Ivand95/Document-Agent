import os
import sys
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from core.config import CATEGORY_LABELS, DEPARTMENT_LIST, get_department_categories  # noqa: E402
from rbac import catalog, legacy, service  # noqa: E402
from rbac.model import Access, Grant, Mapping, Policy, Subject, merge_constraints  # noqa: E402
from rbac_fixtures import FakeRBAC, role_id, rule, seed_data, seed_policy  # noqa: E402

POSITIONS = (None, "", "Analista", "GR - Director", "gr - director", " GR - Director", "GR-Director", "Director GR - ")
DEPARTMENTS = (*DEPARTMENT_LIST, None, "", " ", "Desconocido", "General")
ADMIN_AREAS_OF_ADMIN = ["ingestion", "users", "rbac", "audit", "capabilities"]  # 0005 + 0006


def access(department="Tecnología", position="Analista", status="active", roles=(), rules=(), policy=None,
           legacy_keys=(), now=None):
    grants = tuple(Grant(role_id(k)) if isinstance(k, str) else k for k in roles)
    subject = Subject("u1", status, 1, department, position, grants, tuple(rules))
    return Access(subject, policy or seed_policy(), legacy_keys, now)


class NoLegacyEnv(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {})
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in ("ADMIN_DEPARTMENTS", "AUDIO_ALLOWED_DEPARTMENTS", "RBAC_LEGACY_ENV"):
            os.environ.pop(name, None)


class SeedTest(unittest.TestCase):
    """Migration 0005 must reproduce the rules that were hard-coded before, so nobody notices a change."""

    def test_seed_references_only_its_own_roles_and_known_resources(self):
        data = seed_data()
        keys = {r[0] for r in data["roles"]}
        self.assertTrue({m[2] for m in data["mappings"]} <= keys)
        self.assertTrue({p[0] for p in data["permissions"]} <= keys)
        for _, rtype, rid, action in data["permissions"]:
            catalog.validate_resource(rtype, rid)
            self.assertIn(action, catalog.ACTIONS)
        self.assertTrue(all(r[3] is True for r in data["roles"]))  # every seeded role is a system role
        for key in ("super_admin", "admin", "manager", "employee", "audio_user", "report_user", "tool_admin"):
            self.assertIn(key, keys)

    def test_every_department_has_its_own_role(self):
        mapped = {m[1] for m in seed_data()["mappings"] if m[0] == "department" and m[2].startswith("dept_")}
        self.assertEqual(mapped, set(DEPARTMENT_LIST))

    def test_document_categories_match_the_old_rule_for_everyone(self):
        for department in DEPARTMENTS:
            for position in POSITIONS:
                self.assertEqual(
                    sorted(access(department, position).doc_categories()),
                    sorted(set(get_department_categories(department, position))),
                    (department, position),
                )

    def test_department_names_ignore_accents_and_case_like_the_admin_rule(self):
        # The old category rule compared exactly; the admin rule didn't. RBAC uses the forgiving one.
        self.assertEqual(sorted(access("tecnologia ").doc_categories()), ["IT", "OTROS"])
        self.assertEqual(sorted(access("GESTIÓN HUMANA").doc_categories()), ["HR", "OTROS"])

    def test_admin_areas_match_the_old_default(self):
        self.assertEqual(access("Tecnología").allowed_ids("admin", "admin_area", catalog.ADMIN_AREAS),
                         ADMIN_AREAS_OF_ADMIN)
        self.assertEqual(access("tecnologia").allowed_ids("admin", "admin_area", catalog.ADMIN_AREAS),
                         ADMIN_AREAS_OF_ADMIN)
        for department in DEPARTMENTS:
            if department and department.strip() != "Tecnología":
                self.assertEqual(access(department, "GR - Director").allowed_ids("admin", "admin_area"), [])

    def test_agents_match_the_old_rules(self):
        for department in DEPARTMENTS:
            for position in POSITIONS:
                a = access(department, position)
                self.assertTrue(a.can("use", "agent", "documents"))
                self.assertEqual(a.can("use", "agent", "audio"), bool(position and position.startswith("GR -")))

    def test_super_admin_can_do_everything(self):
        a = access("Marketing", roles=["super_admin"])
        for rtype in catalog.RESOURCE_TYPES:
            for action in catalog.ACTIONS:
                self.assertTrue(a.can(action, rtype, "anything"), (rtype, action))
        self.assertTrue(a.is_super_admin)

    def test_tool_admin_holds_the_code_tools_area_only(self):
        a = access("Marketing", roles=["tool_admin"])
        self.assertEqual(a.allowed_ids("admin", "admin_area"), ["code_tools"])


class EvaluationTest(unittest.TestCase):
    def test_deny_by_default(self):
        empty = Policy(roles={})
        a = access(policy=empty)
        self.assertFalse(a.can("use", "agent", "documents"))
        self.assertEqual(a.check("use", "agent", "documents").reason, "no_rule")
        self.assertEqual(a.doc_categories(), [])

    def test_inactive_users_get_nothing(self):
        for status in ("pending", "disabled"):
            a = access(roles=["super_admin"], status=status)
            self.assertFalse(a.can("use", "agent", "documents"))
            self.assertEqual(a.check("use", "agent", "documents").reason, "inactive")
            self.assertEqual(a.roles, [])

    def test_role_deny_beats_role_allow(self):
        policy = seed_policy()
        rules = dict(policy.role_rules)
        rules[role_id("dept_marketing")] = rules[role_id("dept_marketing")] + (
            rule("doc_category", "OTROS", effect="deny", subject_type="role", subject_id=role_id("dept_marketing")),
        )
        a = access("Marketing", policy=Policy(policy.roles, policy.mappings, rules))
        self.assertEqual(a.doc_categories(), ["MKT"])
        self.assertEqual(a.check("use", "doc_category", "OTROS").reason, "role_deny")

    def test_user_rules_override_role_rules(self):
        a = access("Marketing", rules=[rule("agent", "documents", effect="deny"), rule("agent", "audio")])
        self.assertFalse(a.can("use", "agent", "documents"))
        self.assertEqual(a.check("use", "agent", "documents").reason, "user_deny")
        self.assertTrue(a.can("use", "agent", "audio"))
        self.assertEqual(a.check("use", "agent", "audio").reason, "user_allow")

    def test_user_deny_beats_user_allow_and_wildcards_match(self):
        a = access("Marketing", rules=[rule("doc_category", "*"), rule("doc_category", "STAFF GERENCIAL", effect="deny")])
        self.assertIn("FI", a.doc_categories())
        self.assertNotIn("STAFF GERENCIAL", a.doc_categories())
        b = access("Marketing", rules=[rule("doc_category", "*", effect="deny")])
        self.assertEqual(b.doc_categories(), [])

    def test_actions_are_independent(self):
        a = access("Marketing", rules=[rule("admin_area", "ingestion", action="admin")])
        self.assertTrue(a.can("admin", "admin_area", "ingestion"))
        self.assertFalse(a.can("use", "admin_area", "ingestion"))
        self.assertFalse(a.can("admin", "admin_area", "users"))

    def test_expired_role_assignments_are_ignored(self):
        now = datetime.now(timezone.utc)
        expired = access("Marketing", roles=[Grant(role_id("audio_user"), now - timedelta(minutes=1))])
        current = access("Marketing", roles=[Grant(role_id("audio_user"), now + timedelta(days=1))])
        self.assertFalse(expired.can("use", "agent", "audio"))
        self.assertTrue(current.can("use", "agent", "audio"))

    def test_roles_explain_where_they_come_from(self):
        a = access("Tecnología", "GR - Director", roles=["audio_user"], legacy_keys=[("admin", "legacy:ADMIN_DEPARTMENTS")])
        sources = {h.role.key: h.sources for h in a.roles}
        self.assertEqual(sources["employee"], ("everyone",))
        self.assertEqual(sources["manager"], ("position_prefix:GR -",))
        self.assertEqual(sources["admin"], ("department:Tecnología", "legacy:ADMIN_DEPARTMENTS"))
        self.assertEqual(sources["audio_user"], ("assigned",))

    def test_unknown_roles_in_grants_or_mappings_are_ignored(self):
        policy = Policy({}, (Mapping("m", "everyone", "", "ghost"),), {})
        a = access(policy=policy, roles=[Grant("ghost")])
        self.assertEqual(a.roles, [])

    def test_position_prefix_is_exact_and_empty_prefix_matches_nobody(self):
        m = Mapping("m", "position_prefix", "", "r")
        self.assertFalse(m.matches("X", "Analista"))
        self.assertFalse(Mapping("m", "position_prefix", "GR -", "r").matches("X", None))
        self.assertFalse(Mapping("m", "department", "Marketing", "r").matches(None, "x"))

    def test_constraints_merge_to_the_most_permissive(self):
        self.assertEqual(merge_constraints([rule("model", "m", row_limit=10), rule("model", "m", row_limit=50)]),
                         {"row_limit": 50})
        self.assertEqual(merge_constraints([rule("model", "m", row_limit=10), rule("model", "m")]), {})
        self.assertEqual(merge_constraints([rule("model", "m", ids=["a"]), rule("model", "m", ids=["b", "a"])]),
                         {"ids": ["a", "b"]})
        a = access("Marketing", rules=[rule("model", "x", llm_budget_usd_month=20)])
        self.assertEqual(a.check("use", "model", "x").constraints, {"llm_budget_usd_month": 20})

    def test_summary_for_the_ui(self):
        s = access("Tecnología", "GR - Director").summary()
        self.assertEqual(s["agents"], ["documents", "audio"])
        self.assertEqual(s["admin_areas"], ADMIN_AREAS_OF_ADMIN)
        self.assertEqual(set(s["doc_categories"]), set(CATEGORY_LABELS))
        self.assertIn("manager", s["roles"])

    def test_explain_lists_every_known_resource(self):
        e = access("Marketing").explain()
        decisions = {(d["resource_type"], d["resource_id"]): d for d in e["decisions"]}
        self.assertTrue(decisions[("doc_category", "MKT")]["allowed"])
        self.assertEqual(decisions[("doc_category", "FI")]["reason"], "no_rule")
        self.assertEqual(len(decisions), len(catalog.AGENTS) + len(catalog.ADMIN_AREAS) + len(CATEGORY_LABELS))


class LegacyFallbackTest(NoLegacyEnv):
    def test_unset_variables_add_nothing(self):
        self.assertEqual(legacy.legacy_role_keys("Tecnología"), [])

    def test_set_variables_add_roles_ignoring_accents(self):
        os.environ.update(ADMIN_DEPARTMENTS="Tecnología, Auditoría", AUDIO_ALLOWED_DEPARTMENTS="Cumplimiento")
        self.assertEqual(legacy.legacy_role_keys("auditoria"), [("admin", "legacy:ADMIN_DEPARTMENTS")])
        self.assertEqual(legacy.legacy_role_keys("Cumplimiento"), [("audio_user", "legacy:AUDIO_ALLOWED_DEPARTMENTS")])
        self.assertEqual(legacy.legacy_role_keys(""), [])
        self.assertEqual(legacy.legacy_role_keys(None), [])

    def test_can_be_turned_off(self):
        os.environ.update(ADMIN_DEPARTMENTS="Auditoria", RBAC_LEGACY_ENV="false")
        self.assertEqual(legacy.legacy_role_keys("Auditoria"), [])

    def test_legacy_roles_still_obey_denies(self):
        os.environ.update(ADMIN_DEPARTMENTS="Auditoria")
        a = access("Auditoria", legacy_keys=legacy.legacy_role_keys("Auditoria"),
                   rules=[rule("admin_area", "users", action="admin", effect="deny")])
        self.assertTrue(a.can("admin", "admin_area", "ingestion"))
        self.assertFalse(a.can("admin", "admin_area", "users"))


class ServiceCacheTest(NoLegacyEnv):
    def setUp(self):
        super().setUp()
        self.rbac = FakeRBAC().install(self)

    def test_unknown_or_malformed_ids(self):
        self.assertIsNone(service.access_for("not-a-uuid"))
        self.assertIsNone(service.access_for(str(uuid.uuid4())))
        self.assertIsNone(service.access_for(None))

    def test_users_are_cached_and_invalidated(self):
        uid = self.rbac.add_user("Marketing")
        service.access_for(uid)
        service.access_for(uid)
        self.assertEqual(self.rbac.loads, 1)
        service.invalidate(uid)
        service.access_for(uid)
        self.assertEqual(self.rbac.loads, 2)

    def test_cache_expires(self):
        uid = self.rbac.add_user("Marketing")
        service.access_for(uid)
        with mock.patch.object(service, "CACHE_SECONDS", 0):
            service.access_for(uid)
        self.assertEqual(self.rbac.loads, 2)

    def test_database_errors_propagate_so_callers_fail_closed(self):
        uid = self.rbac.add_user("Marketing")
        self.rbac.fail = True
        with self.assertRaises(RuntimeError):
            service.access_for(uid)

    def test_legacy_env_is_applied(self):
        os.environ["AUDIO_ALLOWED_DEPARTMENTS"] = "Marketing"
        self.assertTrue(service.access_for(self.rbac.add_user("Marketing")).can("use", "agent", "audio"))


class FakeTable:
    """Just enough of the PostgREST query builder: select, eq, in_, order, range, execute."""

    def __init__(self, rows):
        self.rows = list(rows)

    def select(self, *_):
        return self

    def eq(self, column, value):
        self.rows = [r for r in self.rows if str(r.get(column)) == str(value)]
        return self

    def in_(self, column, values):
        self.rows = [r for r in self.rows if r.get(column) in values]
        return self

    def order(self, *_, **__):
        return self

    def range(self, *_):
        return self

    def execute(self):
        return mock.Mock(data=self.rows)


class LoaderTest(unittest.TestCase):
    """rbac.service reads PostgREST rows into the model (the queries themselves are checked on Postgres)."""

    def setUp(self):
        self.uid, self.role = str(uuid.uuid4()), str(uuid.uuid4())
        soon = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        self.tables = {
            "roles": [{"id": self.role, "key": "audio_user", "name": "Llamadas", "description": None, "is_system": True}],
            "role_mappings": [{"id": "m1", "kind": "department", "value": "Marketing", "role_id": self.role}],
            "permissions": [
                {"id": "p1", "subject_type": "role", "subject_id": self.role, "resource_type": "agent",
                 "resource_id": "audio", "action": "use", "effect": "allow", "constraints": None},
                {"id": "p2", "subject_type": "user", "subject_id": self.uid, "resource_type": "doc_category",
                 "resource_id": "FI", "action": "use", "effect": "allow", "constraints": {"row_limit": 5}},
            ],
            "users": [{"id": self.uid, "status": "active", "perm_version": 4, "department": "Marketing", "position": "x"}],
            "user_roles": [{"user_id": self.uid, "role_id": self.role, "expires_at": soon, "granted_by": "a"}],
        }
        client = mock.Mock()
        client.schema.return_value.table.side_effect = lambda name: FakeTable(self.tables[name])
        patcher = mock.patch.object(service, "global_supabase_client", client)
        patcher.start()
        self.addCleanup(patcher.stop)
        service.invalidate()
        self.addCleanup(service.invalidate)

    def test_loads_policy_and_subject(self):
        policy = service.load_policy()
        self.assertEqual(policy.role_by_key("audio_user").id, self.role)
        self.assertEqual([r.id for r in policy.role_rules[self.role]], ["p1"])  # user rules are not policy
        user = service.load_subject(self.uid)
        self.assertEqual((user.perm_version, user.department), (4, "Marketing"))
        self.assertEqual(user.grants[0].expires_at.tzinfo is not None, True)
        self.assertEqual([r.id for r in user.rules], ["p2"])
        a = service.access_for(self.uid)
        self.assertTrue(a.can("use", "agent", "audio"))
        self.assertEqual(a.check("use", "doc_category", "FI").constraints, {"row_limit": 5})
        self.assertIsNone(service.load_subject(str(uuid.uuid4())))

    def test_super_admin_count_skips_expired_and_inactive(self):
        self.tables["roles"].append({"id": "sa", "key": "super_admin", "name": "S", "is_system": True})
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        self.tables["users"] += [{"id": "u2", "status": "active"}, {"id": "u3", "status": "disabled"}, {"id": "u4", "status": "active"}]
        self.tables["user_roles"] += [{"user_id": "u2", "role_id": "sa", "expires_at": None},
                                      {"user_id": "u3", "role_id": "sa", "expires_at": None},
                                      {"user_id": "u4", "role_id": "sa", "expires_at": past}]
        self.assertEqual(service.active_super_admins(), ["u2"])


class CatalogTest(unittest.TestCase):
    def test_validation(self):
        rbac = FakeRBAC().install(self)
        rbac.db.tables["capabilities"] = [{"id": str(uuid.uuid4()), "key": "sql_query", "kind": "tool", "name": "SQL"}]
        catalog.validate_resource("agent", "documents")
        catalog.validate_resource("doc_category", "*")
        catalog.validate_resource("capability", "sql_query")
        catalog.validate_resource("capability", "*")
        catalog.validate_resource("model", "anything-registered-later")  # models aren't checked yet (WP4b)
        self.assertIn({"id": "sql_query", "label": "SQL"}, catalog.catalog()["resources"]["capability"])
        for rtype, rid in (("agent", "nope"), ("doc_category", "XX"), ("weird", "x"), ("model", ""), ("model", "x" * 201),
                           ("capability", "not_registered")):
            with self.assertRaises(ValueError):
                catalog.validate_resource(rtype, rid)


class MePermissionsTest(NoLegacyEnv):
    def setUp(self):
        super().setUp()
        os.environ.update(APP_ENV="test")
        self.client = TestClient(main.app)
        self.rbac = FakeRBAC().install(self)

    def test_returns_the_summary(self):
        r = self.client.get("/me/permissions", headers=self.rbac.headers("Marketing"))
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["agents"], ["documents"])
        self.assertEqual(body["admin_areas"], [])
        self.assertEqual(sorted(body["doc_categories"]), ["MKT", "OTROS"])

    def test_inactive_users_and_database_errors(self):
        self.assertEqual(self.client.get("/me/permissions", headers=self.rbac.headers(status="pending")).status_code, 401)
        h = self.rbac.headers()
        self.rbac.fail = True
        service.invalidate()
        self.assertEqual(self.client.get("/me/permissions", headers=h).status_code, 503)


class FakeStore:
    """Stands in for the rbac.service functions the admin API uses."""

    def __init__(self, rbac: FakeRBAC):
        self.rbac = rbac
        self.roles = {r.id: {"id": r.id, "key": r.key, "name": r.name, "is_system": r.is_system}
                      for r in rbac.policy.roles.values()}
        self.user_roles: dict = {}  # user id -> set of role ids (by hand)
        self.calls: list = []

    def get_role(self, role_id):
        return self.roles.get(str(role_id))

    def list_roles(self):
        return list(self.roles.values())

    def list_user_roles(self, user_id):
        return [{"role_id": r} for r in self.user_roles.get(str(user_id), set())]

    def user_exists(self, user_id):
        return str(user_id) in self.rbac.subjects

    def active_super_admins(self):
        sa = role_id("super_admin")
        return [u for u, roles in self.user_roles.items() if sa in roles]

    def record(self, name):
        def call(*args, **kwargs):
            self.calls.append((name, args))
            if name == "revoke_role":
                self.user_roles.get(str(args[0]), set()).discard(str(args[1]))
                return True
            if name == "assign_role":
                self.user_roles.setdefault(str(args[0]), set()).add(str(args[1]))
            if name == "delete_mapping" or name == "delete_role" or name == "delete_permission":
                return True
            return {"id": "new", "key": args[0] if name == "create_role" else None, "kind": "department", "value": "x"}
        return call


class AdminRbacApiTest(NoLegacyEnv):
    def setUp(self):
        super().setUp()
        os.environ.update(APP_ENV="test")
        self.client = TestClient(main.app)
        self.rbac = FakeRBAC().install(self)
        self.store = FakeStore(self.rbac)
        for name in ("get_role", "list_roles", "list_user_roles", "user_exists", "active_super_admins"):
            self.patch(name, getattr(self.store, name))
        for name in ("create_role", "update_role", "delete_role", "assign_role", "revoke_role", "add_mapping",
                     "delete_mapping", "set_permission", "delete_permission"):
            self.patch(name, self.store.record(name))
        self.target = self.rbac.add_user("Marketing")
        self.admin_claims = self.rbac.claims("Tecnología")  # admin through the seeded mapping
        self.admin = self.bearer(self.admin_claims)

    def patch(self, name, value):
        patcher = mock.patch.object(service, name, value)
        patcher.start()
        self.addCleanup(patcher.stop)

    def bearer(self, claims):
        from core import security
        return {"Authorization": f"Bearer {security.create_access_token(claims)}"}

    def put_permission(self, headers, **overrides):
        body = {"subject_type": "user", "subject_id": self.target, "resource_type": "doc_category",
                "resource_id": "FI", "action": "use", "effect": "allow", **overrides}
        return self.client.put("/admin/rbac/permissions", json=body, headers=headers)

    def test_needs_the_rbac_admin_area(self):
        h = self.rbac.headers("Marketing", "GR - Director")
        for method, path in (("get", "/admin/rbac/roles"), ("get", "/admin/rbac/catalog"),
                             ("get", f"/admin/rbac/users/{self.target}/effective")):
            self.assertEqual(self.client.request(method.upper(), path, headers=h).status_code, 403, path)
        self.assertEqual(self.put_permission(h).status_code, 403)
        self.assertEqual(self.store.calls, [])
        self.assertEqual(self.rbac.audit[-1][:4], ("access.denied", "admin_area", "rbac", "denied"))

    def test_set_a_permission_and_audit_it(self):
        r = self.put_permission(self.admin)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.store.calls[0][0], "set_permission")
        self.assertEqual(self.rbac.audit[-1][:3], ("permission.set", "user", self.target))

    def test_unknown_resources_and_bad_input_are_rejected(self):
        with mock.patch.object(service, "set_permission", side_effect=ValueError("x")):
            self.assertEqual(self.put_permission(self.admin, resource_id="NOPE").status_code, 422)
        self.assertEqual(self.put_permission(self.admin, action="root").status_code, 422)
        self.assertEqual(self.put_permission(self.admin, constraints={"x": "y" * 5000}).status_code, 422)

    def test_nobody_changes_their_own_roles_or_permissions(self):
        me = self.admin_claims["user_id"]
        self.assertEqual(self.put_permission(self.admin, subject_id=me).status_code, 400)
        r = self.client.post(f"/admin/rbac/users/{me}/roles", json={"role_id": role_id("audio_user")}, headers=self.admin)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.store.calls, [])

    def test_only_super_admins_touch_super_admin(self):
        sa = role_id("super_admin")
        r = self.client.post(f"/admin/rbac/users/{self.target}/roles", json={"role_id": sa}, headers=self.admin)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.put_permission(self.admin, subject_type="role", subject_id=sa).status_code, 403)
        self.assertEqual(self.client.post("/admin/rbac/mappings", json={"kind": "department", "value": "Marketing", "role_id": sa},
                                          headers=self.admin).status_code, 403)
        self.assertEqual(self.put_permission(self.admin, resource_id="*").status_code, 403)
        self.assertEqual(self.store.calls, [])

        root = self.bearer(self.rbac.claims("Marketing", roles=["super_admin"]))
        r = self.client.post(f"/admin/rbac/users/{self.target}/roles", json={"role_id": sa}, headers=root)
        self.assertEqual(r.status_code, 201)
        self.assertEqual(self.put_permission(root, resource_id="*").status_code, 200)

    def test_plain_admins_cannot_change_a_super_admin_user(self):
        self.store.user_roles[self.target] = {role_id("super_admin")}
        self.assertEqual(self.put_permission(self.admin, effect="deny").status_code, 403)
        r = self.client.delete(f"/admin/rbac/users/{self.target}/roles/{role_id('employee')}", headers=self.admin)
        self.assertEqual(r.status_code, 403)

    def test_the_last_super_admin_stays(self):
        sa = role_id("super_admin")
        self.store.user_roles[self.target] = {sa}
        root = self.bearer(self.rbac.claims("Marketing", roles=["super_admin"]))
        r = self.client.delete(f"/admin/rbac/users/{self.target}/roles/{sa}", headers=root)
        self.assertEqual(r.status_code, 409)
        other = self.rbac.add_user("Marketing")
        self.store.user_roles[other] = {sa}
        r = self.client.delete(f"/admin/rbac/users/{self.target}/roles/{sa}", headers=root)
        self.assertEqual(r.status_code, 204)

    def test_system_roles_cannot_be_deleted_but_custom_ones_can(self):
        r = self.client.delete(f"/admin/rbac/roles/{role_id('manager')}", headers=self.admin)
        self.assertEqual(r.status_code, 400)
        custom = str(uuid.uuid4())
        self.store.roles[custom] = {"id": custom, "key": "contadores", "name": "C", "is_system": False}
        self.assertEqual(self.client.delete(f"/admin/rbac/roles/{custom}", headers=self.admin).status_code, 204)
        self.assertEqual(self.rbac.audit[-1][0], "role.delete")

    def test_create_role_validates_the_key(self):
        bad = self.client.post("/admin/rbac/roles", json={"key": "Bad Key", "name": "x"}, headers=self.admin)
        self.assertEqual(bad.status_code, 422)
        good = self.client.post("/admin/rbac/roles", json={"key": "contadores", "name": "Contadores"}, headers=self.admin)
        self.assertEqual(good.status_code, 201)

    def test_duplicate_rows_are_a_conflict(self):
        from postgrest.exceptions import APIError

        with mock.patch.object(service, "create_role", side_effect=APIError({"code": "23505", "message": "dup"})):
            r = self.client.post("/admin/rbac/roles", json={"key": "contadores", "name": "C"}, headers=self.admin)
        self.assertEqual(r.status_code, 409)

    def test_role_expiry_must_be_in_the_future(self):
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        r = self.client.post(f"/admin/rbac/users/{self.target}/roles",
                             json={"role_id": role_id("audio_user"), "expires_at": past}, headers=self.admin)
        self.assertEqual(r.status_code, 422)

    def test_effective_permissions(self):
        r = self.client.get(f"/admin/rbac/users/{self.target}/effective", headers=self.admin)
        self.assertEqual(r.status_code, 200)
        self.assertIn("dept_marketing", [role["key"] for role in r.json()["roles"]])
        self.assertEqual(self.client.get(f"/admin/rbac/users/{uuid.uuid4()}/effective", headers=self.admin).status_code, 404)

    def test_audit_viewer_needs_its_area(self):
        with mock.patch("rbac.audit.list_entries", return_value=[{"action": "x"}]) as entries:
            self.assertEqual(self.client.get("/admin/audit", headers=self.admin).json(), {"entries": [{"action": "x"}]})
            self.assertEqual(self.client.get("/admin/audit", headers=self.rbac.headers("Marketing")).status_code, 403)
        entries.assert_called_once()


if __name__ == "__main__":
    unittest.main()
