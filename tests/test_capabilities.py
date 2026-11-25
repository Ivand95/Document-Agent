"""Capability registry (WP4): secrets, configuration rules, native-tool sync, the gateway, the admin API,
and the LLM provider layer (WP4b step 1)."""
import asyncio
import os
import sys
import time
import unittest
import uuid
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from cryptography.fernet import Fernet  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from capabilities import catalog, configs, gateway, native, registry, secrets  # noqa: E402
from capabilities.gateway import CapabilityError  # noqa: E402
from core import llm, security  # noqa: E402
from rbac import service  # noqa: E402
from rbac_fixtures import FakeRBAC, rule  # noqa: E402

KEY = Fernet.generate_key().decode()
OTHER_KEY = Fernet.generate_key().decode()


class EnvCase(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {"APP_ENV": "test", "CAPABILITY_SECRET_KEY": KEY})
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in ("ADMIN_DEPARTMENTS", "AUDIO_ALLOWED_DEPARTMENTS", "RBAC_LEGACY_ENV", "OPENROUTER_ENABLED"):
            os.environ.pop(name, None)


# --- Secrets --------------------------------------------------------------------------------------------


class SecretsTest(EnvCase):
    def test_round_trip_and_ciphertext_hides_the_value(self):
        token = secrets.encrypt({"token": "abc-123"})
        self.assertNotIn("abc-123", token)
        self.assertEqual(secrets.decrypt(token), {"token": "abc-123"})

    def test_missing_or_malformed_key(self):
        os.environ.pop("CAPABILITY_SECRET_KEY")
        self.assertFalse(secrets.configured())
        self.assertEqual(secrets.setting_problems(), [])
        with self.assertRaises(secrets.SecretsUnavailable):
            secrets.encrypt({"token": "x"})
        os.environ["CAPABILITY_SECRET_KEY"] = "not-a-key"
        self.assertEqual(len(secrets.setting_problems()), 1)
        with self.assertRaises(secrets.SecretsUnavailable):
            secrets.encrypt({"token": "x"})

    def test_another_key_cannot_read_it(self):
        token = secrets.encrypt({"token": "x"})
        os.environ["CAPABILITY_SECRET_KEY"] = OTHER_KEY
        with self.assertRaises(secrets.SecretUnreadable):
            secrets.decrypt(token)

    def test_rotation(self):
        old = secrets.encrypt({"token": "x"})
        os.environ["CAPABILITY_SECRET_KEY"] = f"{OTHER_KEY},{KEY}"  # new first, old still readable
        self.assertEqual(secrets.decrypt(old), {"token": "x"})
        new = secrets.rotate(old)
        os.environ["CAPABILITY_SECRET_KEY"] = OTHER_KEY  # old key removed
        self.assertEqual(secrets.decrypt(new), {"token": "x"})

    def test_production_refuses_a_malformed_key(self):
        os.environ.update(APP_ENV="production", JWT_SECRET_KEY="x" * 40, FRONTEND_URL="https://a.example.com",
                          CAPABILITY_SECRET_KEY="bad")
        with self.assertRaisesRegex(RuntimeError, "CAPABILITY_SECRET_KEY"):
            security.validate_settings()
        os.environ["CAPABILITY_SECRET_KEY"] = KEY
        security.validate_settings()


# --- What admins may store ------------------------------------------------------------------------------


class ConfigRulesTest(unittest.TestCase):
    def test_credentials_are_spotted_by_key_name(self):
        for key in ("password", "api_key", "apiKey", "APIKey", "client_secret", "access_token", "refreshToken",
                    "private_key", "dsn", "Authorization"):
            self.assertTrue(catalog.secret_keys({key: "x"}), key)
        self.assertEqual(catalog.secret_keys({"nested": [{"db": {"password": "x"}}]}), ["nested[0].db.password"])
        for key in ("max_tokens", "url", "host", "keyword", "monkey", "database"):
            self.assertEqual(catalog.secret_keys({key: "x"}), [], key)

    def test_mcp_config(self):
        ok = configs.validate_config("mcp", {"url": "https://mcp.example.com/ads"})
        self.assertEqual(ok["transport"], "streamable_http")
        self.assertEqual(ok["timeout_seconds"], 30)
        configs.validate_config("mcp", {"url": "http://localhost:8080/mcp"})
        configs.validate_config("mcp", {"url": "http://sql-mcp:8000/mcp"})  # a container on the same network
        configs.validate_config("mcp", {"url": "http://10.0.0.5/mcp"})
        configs.validate_config("mcp", {"transport": "stdio", "command": "sql-mcp-server", "args": ["--ro"]})
        for bad in (
            {"url": "http://mcp.example.com"},              # plain http to the internet
            {"url": "https://user:pw@mcp.example.com"},     # credentials in the URL
            {"url": "ftp://mcp.example.com"},
            {},                                             # no URL
            {"url": "https://mcp.example.com", "headers": {"x": "y"}},  # unknown field
            {"url": "https://mcp.example.com", "api_key": "x"},         # a credential
            {"transport": "stdio", "command": "/bin/sh"},
            {"transport": "stdio", "command": "../tool"},
            {"transport": "stdio", "command": "tool", "url": "https://x.example.com"},
        ):
            with self.assertRaises(ValueError, msg=bad):
                configs.validate_config("mcp", bad)

    def test_cli_config(self):
        self.assertEqual(configs.validate_config("cli", {"executable": "sqlcmd"}),
                         {"executable": "sqlcmd", "timeout_seconds": 30})
        for bad in ({"executable": "/usr/bin/sqlcmd"}, {"executable": "a b"}, {"executable": ".."},
                    {"executable": "x", "timeout_seconds": 0}, {}):
            with self.assertRaises(ValueError, msg=bad):
                configs.validate_config("cli", bad)

    def test_native_tool_config_follows_its_schema(self):
        schema = {"type": "object", "properties": {"max_rows": {"type": "integer"}}, "additionalProperties": False}
        configs.validate_config("tool", {"max_rows": 5}, schema)
        with self.assertRaises(ValueError):
            configs.validate_config("tool", {"other": 1}, schema)
        with self.assertRaises(ValueError):
            configs.validate_config("tool", {"token": "x"}, None)

    def test_limits_and_argument_schemas(self):
        self.assertEqual(configs.validate_limits({"timeout_seconds": 10}), {"timeout_seconds": 10})
        for bad in ({"timeout_seconds": 1000}, {"max_output_chars": 5}, {"other": 1}):
            with self.assertRaises(ValueError):
                configs.validate_limits(bad)
        configs.validate_arg_schema({"type": "object", "properties": {"q": {"type": "string"}}})
        for bad in ({"type": "string"}, {"type": "object", "properties": {"q": {"type": "nope"}}}, []):
            with self.assertRaises(ValueError):
                configs.validate_arg_schema(bad)

    def test_secrets_per_auth_type(self):
        configs.validate_secret("token", {"token": "x"})
        configs.validate_secret("dsn", {"password": "x", "user": "ro"})
        configs.validate_secret("oauth", {"refresh_token": "x"})
        for auth_type, bad in (("token", {"other": "x"}), ("token", {"token": ""}), ("token", {"token": 5}),
                               ("none", {"token": "x"}), ("token", {}), ("weird", {"token": "x"})):
            with self.assertRaises(ValueError, msg=(auth_type, bad)):
                configs.validate_secret(auth_type, bad)


# --- A native tool for the tests ------------------------------------------------------------------------


def _echo(args, ctx):
    return {"echo": args["text"], "user": ctx.user_id, "limits": ctx.limits}


async def _slow(args, ctx):
    await asyncio.sleep(3)


def _boom(args, ctx):
    raise RuntimeError("internal detail")


def _big(args, ctx):
    return "x" * 5000


def _uses_secret(args, ctx):
    return ctx.secret(None)["token"][:2]


ECHO_SCHEMA = {
    "type": "object",
    "properties": {"text": {"type": "string"}, "note": {"type": "string", "x-private": True}},
    "required": ["text"],
    "additionalProperties": False,
}

DEMO = native.NativeTool(
    key="demo_tool", name="Demo", description="Herramienta de prueba", data_class="internal",
    operations=(
        native.NativeOperation("echo", "Repite el texto", _echo, ECHO_SCHEMA),
        native.NativeOperation("slow", "Tarda", _slow),
        native.NativeOperation("boom", "Falla", _boom),
        native.NativeOperation("big", "Mucho texto", _big),
        native.NativeOperation("peek", "Lee el secreto", _uses_secret),
        native.NativeOperation("write", "Cambia datos fuera", _echo, ECHO_SCHEMA, read_only=False),
    ),
)


class RegistryCase(EnvCase):
    """FakeRBAC + an in-memory registry with the demo tool synced into it."""

    def setUp(self):
        super().setUp()
        self.rbac = FakeRBAC().install(self)
        native.register(DEMO)
        self.addCleanup(native.unregister, DEMO.key)
        registry.sync_native_tools()
        self.cap = self.row("capabilities", key="demo_tool")

    def row(self, table, **match):
        rows = [r for r in self.rbac.db.tables.get(table, []) if all(r.get(k) == v for k, v in match.items())]
        self.assertEqual(len(rows), 1, (table, match))
        return rows[0]

    def enable(self, *operations, **limits):
        registry.update_capability(self.cap, {"enabled": True}, "test")
        for name in operations:
            registry.update_operation(self.cap["id"], name, {"enabled": True, **({"limits": limits} if limits else {})},
                                      "test")

    def user(self, department="Marketing", allow=("demo_tool",), **kwargs):
        rules = [rule("capability", key) for key in allow] + list(kwargs.pop("rules", ()))
        claims = self.rbac.claims(department, rules=rules, **kwargs)
        return service.access_for(claims["user_id"]), claims

    def call(self, access, claims, operation, args, key="demo_tool"):
        return asyncio.run(gateway.call(access, claims, key, operation, args))


class SyncTest(RegistryCase):
    def test_new_tools_and_operations_start_disabled(self):
        self.assertFalse(self.cap["enabled"])
        self.assertEqual(self.cap["kind"], "tool")
        self.assertEqual(self.cap["data_class"], "internal")
        ops = {o["operation"]: o for o in self.rbac.db.tables["capability_operations"]}
        self.assertEqual(set(ops), {"echo", "slow", "boom", "big", "peek", "write"})
        self.assertFalse(any(o["enabled"] for o in ops.values()))
        self.assertFalse(ops["write"]["read_only"])
        self.assertTrue(ops["echo"]["read_only"])

    def test_resync_keeps_admin_choices_and_follows_the_code(self):
        self.enable("echo")
        registry.update_capability(self.cap, {"data_class": "sensitive"}, "test")
        trimmed = native.NativeTool(key="demo_tool", name="Demo", description="x", version=2, operations=(
            native.NativeOperation("echo", "Nuevo texto", _echo, {"type": "object"}),
            native.NativeOperation("new_op", "Nueva", _echo),
        ))
        native.unregister("demo_tool")
        native.register(trimmed)
        report = registry.sync_native_tools()
        self.assertEqual(report["updated"], ["demo_tool"])
        cap = self.row("capabilities", key="demo_tool")
        self.assertTrue(cap["enabled"])
        self.assertEqual(cap["data_class"], "sensitive")
        self.assertEqual(cap["version"], 2)
        ops = {o["operation"]: o for o in self.rbac.db.tables["capability_operations"]}
        self.assertEqual(set(ops), {"echo", "new_op"})
        self.assertTrue(ops["echo"]["enabled"])
        self.assertEqual(ops["echo"]["description"], "Nuevo texto")
        self.assertFalse(ops["new_op"]["enabled"])

    def test_tools_gone_from_the_code_are_reported_not_deleted(self):
        native.unregister("demo_tool")
        report = registry.sync_native_tools()
        self.assertEqual(report["missing"], ["demo_tool"])
        self.row("capabilities", key="demo_tool")

    def test_a_native_key_taken_by_another_kind_is_skipped(self):
        native.unregister("demo_tool")
        self.rbac.db.tables["capabilities"] = []
        registry.create_capability("demo_tool", "cli", "CLI", "", "read", "sensitive", {"executable": "x"}, "t")
        native.register(DEMO)
        report = registry.sync_native_tools()
        self.assertEqual(report["added"], [])
        self.assertEqual(self.row("capabilities", key="demo_tool")["kind"], "cli")


# --- The gateway ------------------------------------------------------------------------------------------


class GatewayTest(RegistryCase):
    def test_tool_list_only_has_what_the_user_may_call(self):
        access, _ = self.user()
        self.assertEqual(gateway.tools_for(access), [])  # nothing enabled yet
        self.enable("echo", "write")
        specs = gateway.tools_for(access)
        self.assertEqual([(s.capability_key, s.operation) for s in specs], [("demo_tool", "echo")])  # write: not read-only
        spec = specs[0]
        self.assertEqual(spec.name, "demo_tool__echo")
        self.assertNotIn("x-private", str(spec.parameters))
        self.assertEqual(spec.as_openai_tool()["function"]["parameters"]["required"], ["text"])
        self.assertEqual(spec.data_class, "internal")
        no_permission, _ = self.user(allow=())
        self.assertEqual(gateway.tools_for(no_permission), [])
        self.assertEqual(gateway.allowed_capabilities(access), ["demo_tool"])
        self.assertEqual(gateway.allowed_capabilities(no_permission), [])

    def test_successful_call_is_wrapped_and_audited(self):
        self.enable("echo")
        access, claims = self.user()
        result = self.call(access, claims, "echo", {"text": "hola <b>", "note": "privado"})
        self.assertIn("<tool_result>", result.text)
        self.assertIn('capability="demo_tool"', result.text)
        self.assertIn("hola &lt;b&gt;", result.text)  # can't close the wrapper
        self.assertFalse(result.truncated)
        action, rtype, rid, outcome, details = self.rbac.audit[-1]
        self.assertEqual((action, rtype, rid, outcome), ("capability.call", "capability", "demo_tool", "ok"))
        self.assertEqual(details["args"], {"text": "hola <b>", "note": "[oculto]"})
        self.assertNotIn("echo", str(details.get("output", "")))  # the output is never audited

    def test_refusals(self):
        access, claims = self.user()
        cases = []
        cases.append(("not_found", lambda: self.call(access, claims, "echo", {"text": "x"}, key="nope")))
        cases.append(("disabled", lambda: self.call(access, claims, "echo", {"text": "x"})))
        for code, run in cases:
            with self.assertRaises(CapabilityError) as e:
                run()
            self.assertEqual(e.exception.code, code)
        self.enable("echo", "write")
        for code, op, args, who in (
            ("not_whitelisted", "big", {}, access),
            ("not_whitelisted", "invented", {}, access),
            ("not_read_only", "write", {"text": "x"}, access),
            ("bad_arguments", "echo", {"text": 5}, access),
            ("bad_arguments", "echo", {"text": "x", "extra": 1}, access),
            ("bad_arguments", "echo", "not an object", access),
            ("denied", "echo", {"text": "x"}, self.user(allow=())[0]),
        ):
            with self.assertRaises(CapabilityError, msg=(code, op)) as e:
                self.call(who, claims, op, args)
            self.assertEqual(e.exception.code, code)
            self.assertEqual(self.rbac.audit[-1][3], "denied")

    def test_explicit_deny_beats_a_role_allow(self):
        self.enable("echo")
        access, claims = self.user(allow=(), rules=[rule("capability", "demo_tool", effect="deny")],
                                   roles=["super_admin"])
        with self.assertRaises(CapabilityError) as e:
            self.call(access, claims, "echo", {"text": "x"})
        self.assertEqual(e.exception.code, "denied")

    def test_a_permission_can_restrict_operations(self):
        self.enable("echo", "big")
        access, claims = self.user(allow=(), rules=[rule("capability", "demo_tool", operations=["echo"])])
        self.assertEqual([s.operation for s in gateway.tools_for(access)], ["echo"])
        with self.assertRaises(CapabilityError):
            self.call(access, claims, "big", {})

    def test_the_code_has_the_last_word_on_read_only(self):
        """Even if the table is edited by hand to say read-only, code marked read_only=False never runs."""
        self.enable("write")
        for op in self.rbac.db.tables["capability_operations"]:
            if op["operation"] == "write":
                op["read_only"] = True
        registry.invalidate()
        access, claims = self.user()
        with self.assertRaises(CapabilityError) as e:
            self.call(access, claims, "write", {"text": "x"})
        self.assertEqual(e.exception.code, "not_read_only")

    def test_timeout_failure_and_output_cap(self):
        self.enable("slow", "boom", timeout_seconds=1)
        self.enable("big", max_output_chars=100)
        access, claims = self.user()
        started = time.monotonic()
        with self.assertRaises(CapabilityError) as e:
            self.call(access, claims, "slow", {})
        self.assertEqual(e.exception.code, "timeout")
        self.assertLess(time.monotonic() - started, 2.5)
        with self.assertRaises(CapabilityError) as e:
            self.call(access, claims, "boom", {})
        self.assertEqual(e.exception.code, "failed")
        self.assertNotIn("internal detail", e.exception.message)
        self.assertEqual(self.rbac.audit[-1][3], "error")
        result = self.call(access, claims, "big", {})
        self.assertTrue(result.truncated)
        self.assertLess(len(result.text), 400)

    def test_kinds_without_an_executor_are_hidden_and_refused(self):
        cap = registry.create_capability("ads", "mcp", "Ads", "", "read", "sensitive",
                                         {"url": "https://mcp.example.com"}, "t")
        registry.update_capability(cap, {"enabled": True}, "t")
        self.rbac.db.tables["capability_operations"].append(
            {"capability_id": cap["id"], "operation": "list", "enabled": True, "read_only": True,
             "arg_schema": {"type": "object"}, "limits": {}, "description": ""})
        registry.invalidate()
        access, claims = self.user(allow=("ads",))
        self.assertEqual(gateway.tools_for(access), [])
        with self.assertRaises(CapabilityError) as e:
            self.call(access, claims, "list", {}, key="ads")
        self.assertEqual(e.exception.code, "not_available")

    def test_secret_reader_gives_the_connection_secret(self):
        self.enable("peek")
        conn = registry.create_connection(self.cap["id"], "main", "token", {}, [], None, "t")
        registry.set_secret(conn, {"token": "tk-123"}, "t")
        access, claims = self.user()
        self.assertIn("tk", self.call(access, claims, "peek", {}).text)

    def test_long_tool_names_stay_unique_and_valid(self):
        taken = set()
        a = gateway._tool_name("k" * 40, "op.with:chars-" + "a" * 40, taken)
        b = gateway._tool_name("k" * 40, "op.with:chars-" + "a" * 41, taken)
        self.assertNotEqual(a, b)
        for name in (a, b):
            self.assertRegex(name, r"^[A-Za-z0-9_-]{1,64}$")


# --- Admin API --------------------------------------------------------------------------------------------


class AdminApiTest(RegistryCase):
    def setUp(self):
        super().setUp()
        self.client = TestClient(main.app)
        self.admin = self.rbac.headers("Tecnología")
        self.super = self.rbac.headers("Marketing", roles=["super_admin"])

    def create(self, headers=None, **body):
        payload = {"key": "ads", "kind": "mcp", "name": "Meta Ads", "config": {"url": "https://mcp.example.com/ads"},
                   **body}
        return self.client.post("/admin/capabilities", json=payload, headers=headers or self.admin)

    def test_needs_the_capabilities_admin_area(self):
        r = self.client.get("/admin/capabilities", headers=self.rbac.headers("Marketing"))
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.rbac.audit[-1][:3], ("access.denied", "admin_area", "capabilities"))
        self.assertEqual(self.client.get("/admin/capabilities", headers=self.admin).status_code, 200)

    def test_create_and_list(self):
        r = self.create()
        self.assertEqual(r.status_code, 201, r.text)
        body = r.json()
        self.assertFalse(body["enabled"])
        self.assertEqual(body["data_class"], "sensitive")
        self.assertEqual(body["config"]["transport"], "streamable_http")
        self.assertEqual(self.rbac.audit[-1][:2], ("capability.create", "capability"))
        self.assertEqual(self.create().status_code, 409)
        listed = {c["key"]: c for c in self.client.get("/admin/capabilities", headers=self.admin).json()["capabilities"]}
        self.assertEqual(set(listed), {"ads", "demo_tool"})
        self.assertEqual(listed["demo_tool"]["operations_total"], 6)

    def test_bad_input(self):
        for body in ({"config": {"url": "http://mcp.example.com"}}, {"config": {"url": "https://x.example.com",
                                                                               "api_key": "x"}},
                     {"kind": "code_tool"}, {"kind": "tool"}, {"key": "Bad Key"}):
            self.assertEqual(self.create(**body).status_code, 422, body)

    def test_data_class_below_sensitive_needs_a_super_admin(self):
        self.assertEqual(self.create(data_class="internal").status_code, 403)
        r = self.create(headers=self.super, data_class="internal")
        self.assertEqual(r.status_code, 201)
        cid = r.json()["id"]
        self.assertEqual(self.client.patch(f"/admin/capabilities/{cid}", json={"data_class": "public"},
                                           headers=self.admin).status_code, 403)
        self.assertEqual(self.client.patch(f"/admin/capabilities/{cid}", json={"data_class": "sensitive"},
                                           headers=self.admin).status_code, 200)  # raising is fine

    def test_update_enables_and_bumps_version_on_config_change(self):
        cid = self.create().json()["id"]
        r = self.client.patch(f"/admin/capabilities/{cid}", json={"enabled": True}, headers=self.admin)
        self.assertTrue(r.json()["enabled"])
        self.assertEqual(r.json()["version"], 1)
        r = self.client.patch(f"/admin/capabilities/{cid}", json={"config": {"url": "https://other.example.com"}},
                              headers=self.admin)
        self.assertEqual(r.json()["version"], 2)
        action, _, _, _, details = self.rbac.audit[-1]
        self.assertEqual(action, "capability.update")
        self.assertEqual(details["before"]["config"]["url"], "https://mcp.example.com/ads")

    def test_native_tools_come_from_code(self):
        cid = self.cap["id"]
        self.assertEqual(self.client.delete(f"/admin/capabilities/{cid}", headers=self.admin).status_code, 400)
        r = self.client.patch(f"/admin/capabilities/{cid}/operations/echo", json={"arg_schema": {"type": "object"}},
                              headers=self.admin)
        self.assertEqual(r.status_code, 400)
        r = self.client.patch(f"/admin/capabilities/{cid}/operations/write", json={"read_only": True},
                              headers=self.admin)
        self.assertEqual(r.status_code, 400)
        r = self.client.patch(f"/admin/capabilities/{cid}/operations/echo",
                              json={"enabled": True, "limits": {"timeout_seconds": 5}}, headers=self.admin)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["enabled"])
        self.assertEqual(self.client.post(f"/admin/capabilities/{cid}/operations", json={"operation": "x"},
                                          headers=self.admin).status_code, 400)
        detail = self.client.get(f"/admin/capabilities/{cid}", headers=self.admin).json()
        self.assertTrue(detail["in_code"])
        self.assertEqual(len(detail["operations"]), 6)

    def test_cli_operations(self):
        cid = self.create(key="sqlcmd", kind="cli", config={"executable": "sqlcmd"}).json()["id"]
        r = self.client.post(f"/admin/capabilities/{cid}/operations", headers=self.admin, json={
            "operation": "query", "read_only": True,
            "arg_schema": {"type": "object", "properties": {"sql": {"type": "string"}}}})
        self.assertEqual(r.status_code, 201, r.text)
        self.assertFalse(r.json()["enabled"])
        bad = self.client.post(f"/admin/capabilities/{cid}/operations", headers=self.admin,
                               json={"operation": "x", "arg_schema": {"type": "string"}})
        self.assertEqual(bad.status_code, 422)
        self.assertEqual(self.client.patch(f"/admin/capabilities/{cid}/operations/query", json={"enabled": True},
                                           headers=self.admin).status_code, 200)
        self.assertEqual(self.client.delete(f"/admin/capabilities/{cid}/operations/query",
                                            headers=self.admin).status_code, 204)
        self.assertEqual(self.client.delete(f"/admin/capabilities/{cid}/operations/query",
                                            headers=self.admin).status_code, 404)

    def test_read_only_on_a_risky_capability_needs_a_super_admin(self):
        cid = self.create(key="ghl", risk="write").json()["id"]
        self.rbac.db.tables["capability_operations"].append(
            {"capability_id": cid, "operation": "update_contact", "enabled": False, "read_only": False,
             "arg_schema": {"type": "object"}, "limits": {}, "description": ""})
        path = f"/admin/capabilities/{cid}/operations/update_contact"
        self.assertEqual(self.client.patch(path, json={"read_only": True}, headers=self.admin).status_code, 403)
        self.assertEqual(self.client.patch(path, json={"read_only": True}, headers=self.super).status_code, 200)

    def test_connections_and_write_only_secrets(self):
        cid = self.create().json()["id"]
        bad = self.client.post(f"/admin/capabilities/{cid}/connections", headers=self.admin,
                               json={"name": "main", "auth_type": "dsn", "config": {"password": "x"}})
        self.assertEqual(bad.status_code, 422)
        r = self.client.post(f"/admin/capabilities/{cid}/connections", headers=self.admin,
                             json={"name": "main", "auth_type": "token", "config": {"account": "act_1"}})
        self.assertEqual(r.status_code, 201, r.text)
        conn = r.json()
        self.assertFalse(conn["has_secret"])
        self.assertEqual(self.client.post(f"/admin/capabilities/{cid}/connections", headers=self.admin,
                                          json={"name": "main", "auth_type": "token"}).status_code, 409)

        path = f"/admin/capabilities/connections/{conn['id']}"
        self.assertEqual(self.client.put(f"{path}/secret", json={"secret": {"other": "x"}},
                                         headers=self.admin).status_code, 422)
        with mock.patch.dict(os.environ, {"CAPABILITY_SECRET_KEY": ""}):
            self.assertEqual(self.client.put(f"{path}/secret", json={"secret": {"token": "tk-SECRET"}},
                                             headers=self.admin).status_code, 503)
        r = self.client.put(f"{path}/secret", json={"secret": {"token": "tk-SECRET"}}, headers=self.admin)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["has_secret"])
        self.assertEqual(self.rbac.audit[-1][4], {"fields": ["token"]})

        stored = self.row("connections", id=conn["id"])["secret_ciphertext"]
        self.assertNotIn("tk-SECRET", stored)
        self.assertEqual(secrets.decrypt(stored), {"token": "tk-SECRET"})
        for response in (self.client.get(f"/admin/capabilities/{cid}", headers=self.admin),
                         self.client.get(f"/admin/capabilities/{cid}/connections", headers=self.admin), r):
            self.assertNotIn("tk-SECRET", response.text)
            self.assertNotIn("secret_ciphertext", response.text)
            self.assertNotIn(stored, response.text)
        self.assertNotIn("tk-SECRET", str(self.rbac.audit))

        # No checker for MCP yet (WP5): the secret is verified, the status stays unchecked
        r = self.client.post(f"{path}/check", headers=self.admin)
        self.assertEqual(r.json()["status"], "unchecked")
        self.assertIsNotNone(r.json()["last_check_at"])
        with mock.patch.dict(os.environ, {"CAPABILITY_SECRET_KEY": OTHER_KEY}):
            r = self.client.post(f"{path}/check", headers=self.admin)
        self.assertEqual(r.json()["status"], "error")
        self.assertIn("CAPABILITY_SECRET_KEY", r.json()["status_detail"])

        r = self.client.patch(path, json={"status": "disabled"}, headers=self.admin)
        self.assertEqual(r.json()["status"], "disabled")
        self.assertEqual(self.client.patch(path, json={"status": "ok"}, headers=self.admin).status_code, 422)
        r = self.client.delete(f"{path}/secret", headers=self.admin)
        self.assertFalse(r.json()["has_secret"])

        self.assertEqual(self.client.delete(f"/admin/capabilities/{cid}", headers=self.admin).status_code, 204)
        self.assertEqual(self.rbac.db.tables["connections"], [])  # cascade

    def test_native_tool_health_check(self):
        checked = native.NativeTool(key="checked_tool", name="C", description="", check=lambda config, secret: "Responde",
                                    operations=(native.NativeOperation("op", "", _echo),))
        native.register(checked)
        self.addCleanup(native.unregister, "checked_tool")
        registry.sync_native_tools()
        cap = self.row("capabilities", key="checked_tool")
        conn = registry.create_connection(cap["id"], "c", "none", {}, [], None, "t")
        r = self.client.post(f"/admin/capabilities/connections/{conn['id']}/check", headers=self.admin)
        self.assertEqual((r.json()["status"], r.json()["status_detail"]), ("ok", "Responde"))
        token_conn = registry.create_connection(cap["id"], "t", "token", {}, [], None, "t")
        r = self.client.post(f"/admin/capabilities/connections/{token_conn['id']}/check", headers=self.admin)
        self.assertEqual((r.json()["status"], r.json()["status_detail"]), ("error", "Falta el secreto"))

    def test_provider_connections_are_not_reachable_here(self):
        conn = registry.create_connection(None, "openrouter", "token", {}, [], None, "t")
        r = self.client.patch(f"/admin/capabilities/connections/{conn['id']}", json={"name": "x"}, headers=self.admin)
        self.assertEqual(r.status_code, 404)

    def test_permissions_can_name_registered_capabilities_only(self):
        from rbac import catalog as rbac_catalog

        rbac_catalog.validate_resource("capability", "demo_tool")
        with self.assertRaises(ValueError):  # refused before anything is written
            service.set_permission("role", uuid.uuid4(), "capability", "not_registered", "use", "allow", {}, "t")
        r = self.client.get("/admin/rbac/catalog", headers=self.admin)
        self.assertIn({"id": "demo_tool", "label": "Demo"}, r.json()["resources"]["capability"])

    def test_me_permissions_lists_usable_capabilities(self):
        self.enable("echo")
        r = self.client.get("/me/permissions", headers=self.rbac.headers("Marketing", roles=["super_admin"]))
        self.assertEqual(r.json()["capabilities"], ["demo_tool"])
        r = self.client.get("/me/permissions", headers=self.rbac.headers("Marketing"))
        self.assertEqual(r.json()["capabilities"], [])
        self.rbac.db.fail = True
        registry.invalidate()
        r = self.client.get("/me/permissions", headers=self.rbac.headers("Marketing"))
        self.assertEqual((r.status_code, r.json()["capabilities"]), (200, []))


class MigrationSeedTest(unittest.TestCase):
    def test_0006_gives_the_admin_role_the_registry(self):
        from rbac_fixtures import seed_data

        self.assertIn(("admin", "admin_area", "capabilities", "admin"), seed_data()["permissions"])


# --- LLM providers (WP4b step 1) --------------------------------------------------------------------------


class ProviderTest(EnvCase):
    def setUp(self):
        super().setUp()
        llm.reset_clients()
        self.addCleanup(llm.reset_clients)
        saved = dict(llm._key_resolvers)  # keep the resolver the providers registry registers at import
        llm._key_resolvers.clear()
        self.addCleanup(lambda: (llm._key_resolvers.clear(), llm._key_resolvers.update(saved)))

    def test_model_references(self):
        self.assertEqual(llm.parse_model("gpt-4o"), llm.ModelRef("openai", "gpt-4o"))
        self.assertEqual(llm.parse_model("openai:gpt-4o"), llm.ModelRef("openai", "gpt-4o"))
        self.assertEqual(llm.parse_model("ft:gpt-4o:org:x"), llm.ModelRef("openai", "ft:gpt-4o:org:x"))
        self.assertEqual(llm.parse_model("openrouter:anthropic/claude-x"),
                         llm.ModelRef("openrouter", "anthropic/claude-x"))
        self.assertEqual(str(llm.parse_model("openrouter:a/b")), "openrouter:a/b")
        for bad in ("", "openrouter:", "  "):
            with self.assertRaises(ValueError):
                llm.parse_model(bad)

    def test_core_agents_never_leave_openai(self):
        with mock.patch.object(llm, "client") as client:
            with self.assertRaises(ValueError):
                llm.chat_text_sync([{"role": "user", "content": "x"}], model="openrouter:a/b")
            with self.assertRaises(ValueError):
                llm.chat_json_sync([], "n", {}, model="openrouter:a/b")
            client.assert_not_called()
            client.return_value.chat.completions.create.return_value.choices = [mock.Mock(message=mock.Mock(content="ok"))]
            self.assertEqual(llm.chat_text_sync([], model="openai:gpt-4o-mini"), "ok")
            client.assert_called_with("openai")
            self.assertEqual(client.return_value.chat.completions.create.call_args.kwargs["model"], "gpt-4o-mini")

    def test_openrouter_is_off_by_default_and_needs_a_key(self):
        with self.assertRaisesRegex(llm.ProviderUnavailable, "OPENROUTER_ENABLED"):
            llm.client("openrouter")
        os.environ["OPENROUTER_ENABLED"] = "true"
        with self.assertRaisesRegex(llm.ProviderUnavailable, "clave"):
            llm.client("openrouter")
        llm.register_key_resolver("openrouter", lambda: "or-key")
        c = llm.client("openrouter")
        self.assertEqual(str(c.base_url).rstrip("/"), llm.OPENROUTER_BASE_URL)
        self.assertIs(llm.client("openrouter"), c)
        llm.register_key_resolver("openrouter", lambda: "rotated")
        self.assertIsNot(llm.client("openrouter"), c)  # a new key builds a new client

    def test_openai_client_unchanged(self):
        with mock.patch.object(llm, "LLM_SERVICE", "gemini"):
            with self.assertRaises(RuntimeError):
                llm.client()
        with mock.patch.object(llm, "LLM_SERVICE", "openai"), mock.patch.object(llm, "LLM_API_KEY", "sk-test"):
            self.assertIn("api.openai.com", str(llm.client().base_url))
        with self.assertRaises(ValueError):
            llm.register_key_resolver("openai", lambda: "x")


if __name__ == "__main__":
    unittest.main()
