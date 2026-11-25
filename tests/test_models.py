"""LLM providers, model allowlist, data classes, budgets and usage (WP4b step 2)."""
import asyncio
import os
import sys
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from cryptography.fernet import Fernet  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from core import llm  # noqa: E402
from providers import gateway, registry, usage  # noqa: E402
from providers.gateway import ModelError  # noqa: E402
from rbac import catalog, service  # noqa: E402
from rbac_fixtures import FakeRBAC, rule, seed_data  # noqa: E402

KEY = Fernet.generate_key().decode()
OR_KEY = "sk-or-v1-SECRETVALUE"


def response(content="ok", tokens_in=10, tokens_out=5, cost=None, tool_calls=None):
    used = SimpleNamespace(prompt_tokens=tokens_in, completion_tokens=tokens_out)
    if cost is not None:
        used.cost = cost
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=used)


def catalog_entry(model_id, prompt="0.000001", completion="0.000002", params=("tools", "structured_outputs"),
                  outputs=("text",)):
    return {"id": model_id, "name": model_id.title(), "context_length": 128000,
            "pricing": {"prompt": prompt, "completion": completion}, "supported_parameters": list(params),
            "architecture": {"output_modalities": list(outputs)}}


class ModelsCase(unittest.TestCase):
    """FakeRBAC, the seeded providers, and an OpenRouter model enabled for tests."""

    def setUp(self):
        patcher = mock.patch.dict(os.environ, {"APP_ENV": "test", "CAPABILITY_SECRET_KEY": KEY,
                                               "OPENROUTER_ENABLED": "true"})
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in ("ADMIN_DEPARTMENTS", "AUDIO_ALLOWED_DEPARTMENTS", "RBAC_LEGACY_ENV"):
            os.environ.pop(name, None)
        self.rbac = FakeRBAC().install(self)
        self.addCleanup(llm.reset_clients)
        db = self.rbac.db
        db.tables["llm_providers"] = [
            {"id": str(uuid.uuid4()), "key": "openai", "name": "OpenAI", "base_url": "https://api.openai.com/v1",
             "enabled": True, "connection_id": None},
            {"id": str(uuid.uuid4()), "key": "openrouter", "name": "OpenRouter",
             "base_url": "https://openrouter.ai/api/v1", "enabled": True, "connection_id": None},
        ]
        self.openrouter = registry.get_provider("openrouter")
        with mock.patch.object(registry, "fetch_models", return_value=[
            catalog_entry("vendor/fast"), catalog_entry("vendor/plain", params=("temperature",)),
        ]):
            registry.discover(self.openrouter)
        self.model = self.model_row("vendor/fast")
        registry.update_model(self.model, {"enabled": True}, "test")
        registry.set_provider_key(registry.get_provider("openrouter"), OR_KEY, None, "test")

    def model_row(self, model_id):
        return next(r for r in self.rbac.db.tables["llm_models"] if r["model_id"] == model_id)

    def user(self, department="Marketing", allow=("openrouter:vendor/fast",), budget=None, **kwargs):
        constraints = {} if budget is None else {"llm_budget_usd_month": budget}
        rules = [rule("model", ref, **constraints) for ref in allow] + list(kwargs.pop("rules", ()))
        claims = self.rbac.claims(department, rules=rules, **kwargs)
        return service.access_for(claims["user_id"]), claims

    def usage_rows(self):
        return self.rbac.db.tables.get("llm_usage", [])


class DiscoveryTest(ModelsCase):
    def test_parsing(self):
        parsed = registry.parse_model(catalog_entry("a/b", prompt="0.0000025", completion="-1"))
        self.assertEqual(parsed["price_in"], 2.5)
        self.assertIsNone(parsed["price_out"])  # variable price
        self.assertTrue(parsed["supports_tools"] and parsed["supports_json_schema"])
        self.assertIsNone(registry.parse_model(catalog_entry("img/gen", outputs=("image",))))
        self.assertIsNone(registry.parse_model({"name": "no id"}))
        json_mode_only = registry.parse_model(catalog_entry("a/c", params=("response_format",)))
        self.assertFalse(json_mode_only["supports_json_schema"])

    def test_new_models_start_disabled_and_capped(self):
        plain = self.model_row("vendor/plain")
        self.assertFalse(plain["enabled"])
        self.assertEqual(plain["max_data_class"], "internal")
        self.assertEqual(plain["routing"], {"data_collection": "deny"})

    def test_rediscovery_keeps_admin_choices(self):
        registry.update_model(self.model, {"routing": {"only": ["groq"]}}, "test")
        with mock.patch.object(registry, "fetch_models", return_value=[catalog_entry("vendor/fast", prompt="0.000003")]):
            report = registry.discover(self.openrouter)
        self.assertEqual(report, {"found": 1, "added": 0, "unavailable": 1})
        fast = self.model_row("vendor/fast")
        self.assertTrue(fast["enabled"])
        self.assertEqual(fast["routing"], {"data_collection": "deny", "only": ["groq"]})
        self.assertEqual(fast["price_in"], 3.0)
        self.assertFalse(self.model_row("vendor/plain")["available"])

    def test_routing_validation(self):
        self.assertEqual(registry.validate_routing({}), {"data_collection": "deny"})
        self.assertEqual(registry.validate_routing({"zdr": True, "sort": "price"})["zdr"], True)
        for bad in ({"nope": 1}, {"zdr": "yes"}, {"order": "groq"}, {"order": [1]}, {"data_collection": "maybe"},
                    {"preferred_max_latency": True}, []):
            with self.assertRaises(ValueError, msg=bad):
                registry.validate_routing(bad)

    def test_model_ids_become_rbac_resources(self):
        catalog.validate_resource("model", "openrouter:vendor/fast")
        with self.assertRaises(ValueError):
            catalog.validate_resource("model", "openrouter:vendor/unknown")


class KeyTest(ModelsCase):
    def test_key_is_encrypted_and_used_by_the_client(self):
        stored = self.rbac.db.tables["connections"][0]
        self.assertIsNone(stored["capability_id"])
        self.assertNotIn(OR_KEY, stored["secret_ciphertext"])
        self.assertEqual(registry._openrouter_key(), OR_KEY)
        self.assertEqual(llm.client("openrouter").api_key, OR_KEY)

    def test_without_the_flag_or_key_openrouter_is_unavailable(self):
        os.environ["OPENROUTER_ENABLED"] = "false"
        with self.assertRaises(llm.ProviderUnavailable):
            llm.client("openrouter")
        os.environ["OPENROUTER_ENABLED"] = "true"
        registry.clear_provider_key(registry.get_provider("openrouter"), "test")
        with self.assertRaises(llm.ProviderUnavailable):
            llm.client("openrouter")


class GatewayTest(ModelsCase):
    def chat(self, access, ref="openrouter:vendor/fast", data_class="internal", **kwargs):
        return asyncio.run(gateway.chat(access, ref, [{"role": "user", "content": "hola"}], data_class, **kwargs))

    def test_successful_call_sends_routing_and_records_usage(self):
        access, _ = self.user()
        with mock.patch.object(llm, "client") as client:
            client.return_value.chat.completions.create.return_value = response("hecho", 100, 50, cost=0.0021)
            result = self.chat(access, capability_key="summarize", agent_key="reports")
        self.assertEqual((result.content, result.cost_usd, result.tokens_in), ("hecho", 0.0021, 100))
        client.assert_called_with("openrouter")
        kwargs = client.return_value.chat.completions.create.call_args.kwargs
        self.assertEqual(kwargs["model"], "vendor/fast")
        self.assertEqual(kwargs["extra_body"], {"provider": {"data_collection": "deny"}})
        row = self.usage_rows()[-1]
        self.assertEqual((row["user_id"], row["model"], row["capability_key"], row["agent_key"]),
                         (access.user_id, "openrouter:vendor/fast", "summarize", "reports"))
        self.assertEqual((row["cost_usd"], row["cost_source"], row["outcome"]), (0.0021, "provider", "ok"))
        self.assertNotIn("hola", str(row))
        self.assertNotIn("hecho", str(row))

    def test_refusals(self):
        access, _ = self.user()
        cases = [
            ("not_found", dict(ref="openrouter:vendor/unknown")),
            ("disabled", dict(ref="openrouter:vendor/plain")),
            ("data_class", dict(data_class="sensitive")),  # member data never reaches an `internal` model
        ]
        for code, kwargs in cases:
            with self.assertRaises(ModelError, msg=code) as e:
                self.chat(access, **kwargs)
            self.assertEqual(e.exception.code, code)
        registry.update_model(self.model_row("vendor/plain"), {"enabled": True}, "t")
        no_permission, _ = self.user(allow=())
        with self.assertRaises(ModelError) as e:
            self.chat(no_permission)
        self.assertEqual(e.exception.code, "denied")
        plain_user, _ = self.user(allow=("openrouter:vendor/plain",))
        with self.assertRaises(ModelError) as e:
            self.chat(plain_user, ref="openrouter:vendor/plain", tools=[{"type": "function"}])
        self.assertEqual(e.exception.code, "no_tools")
        with self.assertRaises(ModelError) as e:
            self.chat(plain_user, ref="openrouter:vendor/plain", json_schema={"type": "object"})
        self.assertEqual(e.exception.code, "no_json_schema")
        self.assertEqual(self.usage_rows(), [])  # refused before any call

    def test_unavailable_when_switched_off(self):
        access, _ = self.user()
        os.environ["OPENROUTER_ENABLED"] = "false"
        with self.assertRaises(ModelError) as e:
            self.chat(access)
        self.assertEqual(e.exception.code, "unavailable")
        os.environ["OPENROUTER_ENABLED"] = "true"
        registry.set_provider_enabled(registry.get_provider("openrouter"), False, "t")
        with self.assertRaises(ModelError) as e:
            self.chat(access)
        self.assertEqual(e.exception.code, "unavailable")

    def test_a_super_admin_can_raise_a_model_to_sensitive(self):
        registry.update_model(self.model_row("vendor/fast"), {"max_data_class": "sensitive"}, "t")
        access, _ = self.user()
        with mock.patch.object(llm, "client") as client:
            client.return_value.chat.completions.create.return_value = response()
            self.chat(access, data_class="sensitive")

    def test_budget(self):
        access, _ = self.user(budget=0.01)
        with mock.patch.object(llm, "client") as client:
            client.return_value.chat.completions.create.return_value = response(cost=0.006)
            self.chat(access)
            self.chat(access)  # 0.006 spent < 0.01
            with self.assertRaises(ModelError) as e:
                self.chat(access)  # 0.012 spent >= 0.01
        self.assertEqual(e.exception.code, "budget")
        self.assertEqual(usage.month_spend(access.user_id, "openrouter"), 0.012)
        unlimited, _ = self.user()
        self.assertEqual(usage.month_spend(unlimited.user_id, "openrouter"), 0)

    def test_retry_once_then_fail(self):
        access, _ = self.user()
        with mock.patch.object(llm, "client") as client:
            client.return_value.chat.completions.create.side_effect = [RuntimeError("503"), response("tras reintento")]
            self.assertEqual(self.chat(access).content, "tras reintento")
            client.return_value.chat.completions.create.side_effect = RuntimeError("503")
            with self.assertRaises(ModelError) as e:
                self.chat(access)
        self.assertEqual(e.exception.code, "failed")
        self.assertEqual([r["outcome"] for r in self.usage_rows()], ["error", "ok", "error", "error"])

    def test_models_for(self):
        access, _ = self.user()
        self.assertEqual(gateway.models_for(access, "internal"), ["openrouter:vendor/fast"])
        self.assertEqual(gateway.models_for(access, "sensitive"), [])


class CoreUsageTest(ModelsCase):
    def test_core_agent_calls_are_counted_with_the_price_list(self):
        openai = registry.get_provider("openai")
        registry.add_model(openai, "gpt-4o-mini", "", 0.15, 0.6, True, True, None, "t")
        with mock.patch.object(llm, "client") as client, llm.usage_context(user_id="u-1", agent_key="documents"):
            client.return_value.chat.completions.create.return_value = response("x", 1000, 1000)
            llm.chat_text_sync([{"role": "user", "content": "secreto"}], model="gpt-4o-mini")
        row = self.usage_rows()[-1]
        self.assertEqual((row["provider"], row["model"], row["agent_key"]), ("openai", "openai:gpt-4o-mini", "documents"))
        self.assertEqual((row["cost_usd"], row["cost_source"]), (0.00075, "price_list"))
        self.assertNotIn("secreto", str(row))

    def test_unknown_price_and_no_user(self):
        with mock.patch.object(llm, "client") as client:
            client.return_value.chat.completions.create.return_value = response()
            llm.chat_text_sync([], model="gpt-4o")
        row = self.usage_rows()[-1]
        self.assertIsNone(row["cost_usd"])
        self.assertIsNone(row["user_id"])

    def test_a_failing_store_never_breaks_the_answer(self):
        self.rbac.db.fail = True
        with mock.patch.object(llm, "client") as client:
            client.return_value.chat.completions.create.return_value = response("respuesta")
            self.assertEqual(llm.chat_text_sync([], model="gpt-4o"), "respuesta")

    def test_chat_endpoint_sets_the_usage_context(self):
        seen = {}

        class Graph:
            async def ainvoke(self, inputs, config):
                seen["ctx"] = llm._context.get()
                return {"answer": "ok"}

        client = TestClient(main.app)
        claims = self.rbac.claims("Marketing")
        from core import security
        headers = {"Authorization": f"Bearer {security.create_access_token(claims)}"}
        with mock.patch("api.chat.get_app_graph", return_value=Graph()):
            r = client.post("/chat", json={"message": "hola"}, headers=headers)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((seen["ctx"].user_id, seen["ctx"].agent_key), (claims["user_id"], "documents"))
        self.assertIsNone(llm._context.get().user_id)  # reset afterwards


class SummaryTest(ModelsCase):
    def test_month_summary(self):
        now = datetime.now(timezone.utc)
        rows = self.rbac.db.tables.setdefault("llm_usage", [])
        rows += [
            {"at": now.isoformat(), "user_id": "a", "provider": "openrouter", "model": "openrouter:x", "tokens_in": 10,
             "tokens_out": 5, "cost_usd": 0.5, "outcome": "ok", "agent_key": None, "capability_key": None},
            {"at": now.isoformat(), "user_id": "a", "provider": "openai", "model": "openai:gpt-4o", "tokens_in": 1,
             "tokens_out": 1, "cost_usd": None, "outcome": "error", "agent_key": "documents", "capability_key": None},
            {"at": "2020-01-15T00:00:00+00:00", "user_id": "a", "provider": "openai", "model": "openai:gpt-4o",
             "tokens_in": 1, "tokens_out": 1, "cost_usd": 9.0, "outcome": "ok", "agent_key": None, "capability_key": None},
        ]
        by_user = usage.summary(now, "user_id")
        self.assertEqual(by_user, [{"user_id": "a", "calls": 2, "errors": 1, "tokens_in": 11, "tokens_out": 6,
                                    "cost_usd": 0.5, "unpriced_calls": 1}])
        old = usage.summary(datetime(2020, 1, 31, tzinfo=timezone.utc), "model")
        self.assertEqual(old[0]["cost_usd"], 9.0)
        with self.assertRaises(ValueError):
            usage.summary(now, "content")


class AdminApiTest(ModelsCase):
    def setUp(self):
        super().setUp()
        self.client = TestClient(main.app)
        self.admin = self.rbac.headers("Tecnología")
        self.super = self.rbac.headers("Marketing", roles=["super_admin"])

    def test_needs_the_models_admin_area(self):
        self.assertEqual(self.client.get("/admin/models", headers=self.rbac.headers("Marketing")).status_code, 403)
        body = self.client.get("/admin/models?provider=openrouter", headers=self.admin).json()
        self.assertEqual(body["total"], 2)
        self.assertEqual(body["models"][0]["ref"], "openrouter:vendor/fast")  # enabled first

    def test_key_is_write_only(self):
        r = self.client.put("/admin/models/providers/openrouter/key", json={"api_key": "sk-or-v1-NEWSECRET"},
                            headers=self.admin)
        self.assertEqual(r.status_code, 200, r.text)
        for response_ in (r, self.client.get("/admin/models/providers", headers=self.admin)):
            self.assertNotIn("NEWSECRET", response_.text)
            self.assertNotIn("secret_ciphertext", response_.text)
        providers = {p["key"]: p for p in self.client.get("/admin/models/providers", headers=self.admin).json()["providers"]}
        self.assertTrue(providers["openrouter"]["has_key"])
        self.assertNotIn("NEWSECRET", str(self.rbac.audit))
        self.assertEqual(registry._openrouter_key(), "sk-or-v1-NEWSECRET")
        self.assertEqual(self.client.put("/admin/models/providers/openai/key", json={"api_key": "sk-12345678"},
                                         headers=self.admin).status_code, 400)
        # The provider's connection can't be reached through the capabilities API
        conn_id = providers["openrouter"]["connection_id"]
        self.assertEqual(self.client.delete(f"/admin/capabilities/connections/{conn_id}", headers=self.admin).status_code,
                         404)

    def test_raising_to_sensitive_and_allowing_data_collection_need_a_super_admin(self):
        mid = self.model_row("vendor/fast")["id"]
        path = f"/admin/models/{mid}"
        self.assertEqual(self.client.patch(path, json={"max_data_class": "sensitive"}, headers=self.admin).status_code, 403)
        self.assertEqual(self.client.patch(path, json={"max_data_class": "public"}, headers=self.admin).status_code, 200)
        self.assertEqual(self.client.patch(path, json={"max_data_class": "sensitive"}, headers=self.super).status_code, 200)
        self.assertEqual(self.client.patch(path, json={"routing": {"data_collection": "allow"}},
                                           headers=self.admin).status_code, 403)
        r = self.client.patch(path, json={"routing": {"zdr": True}}, headers=self.admin)
        self.assertEqual(r.json()["routing"], {"data_collection": "deny", "zdr": True})
        self.assertEqual(self.client.patch(path, json={"routing": {"bogus": 1}}, headers=self.admin).status_code, 422)
        self.assertEqual(self.client.patch(path, json={"price_in": 1}, headers=self.admin).status_code, 400)
        action, rtype, rid, _, details = self.rbac.audit[-1]
        self.assertEqual((action, rtype, rid), ("model.update", "model", "openrouter:vendor/fast"))

    def test_unavailable_models_cannot_be_enabled(self):
        plain = self.model_row("vendor/plain")
        plain["available"] = False
        r = self.client.patch(f"/admin/models/{plain['id']}", json={"enabled": True}, headers=self.admin)
        self.assertEqual(r.status_code, 409)

    def test_openai_models_by_hand(self):
        r = self.client.post("/admin/models", json={"provider": "openai", "model_id": "gpt-4o", "price_in": 2.5,
                                                     "price_out": 10}, headers=self.admin)
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual((r.json()["max_data_class"], r.json()["enabled"]), ("sensitive", False))
        self.assertEqual(self.client.post("/admin/models", json={"provider": "openai", "model_id": "gpt-4o"},
                                          headers=self.admin).status_code, 409)
        self.assertEqual(self.client.post("/admin/models", json={"provider": "openrouter", "model_id": "x/y"},
                                          headers=self.admin).status_code, 422)
        r = self.client.patch(f"/admin/models/{r.json()['id']}", json={"price_in": 3}, headers=self.admin)
        self.assertEqual(r.json()["price_in"], 3)

    def test_discover_and_check(self):
        with mock.patch.object(registry, "fetch_models", return_value=[catalog_entry("vendor/new")]):
            r = self.client.post("/admin/models/providers/openrouter/discover", headers=self.admin)
        self.assertEqual(r.json()["added"], 1)
        with mock.patch.object(registry, "fetch_models", side_effect=RuntimeError("down")):
            self.assertEqual(self.client.post("/admin/models/providers/openrouter/discover",
                                              headers=self.admin).status_code, 502)
        self.assertEqual(self.client.post("/admin/models/providers/openai/discover", headers=self.admin).status_code, 400)

        ok = mock.Mock(status_code=200, json=lambda: {"data": {"limit_remaining": 12.5, "usage_monthly": 3.1}})
        with mock.patch.object(registry.httpx, "get", return_value=ok) as get:
            r = self.client.post("/admin/models/providers/openrouter/check", headers=self.admin)
        self.assertEqual(r.json()["status"], "ok")
        self.assertIn("12.5", r.json()["status_detail"])
        self.assertEqual(get.call_args.kwargs["headers"]["Authorization"], f"Bearer {OR_KEY}")
        self.assertNotIn(OR_KEY, r.text)
        with mock.patch.object(registry.httpx, "get", return_value=mock.Mock(status_code=401)):
            r = self.client.post("/admin/models/providers/openrouter/check", headers=self.admin)
        self.assertEqual((r.json()["status"], r.json()["status_detail"]), ("error", "La clave no es válida"))

    def test_usage_endpoint(self):
        access, _ = self.user()
        with mock.patch.object(llm, "client") as client:
            client.return_value.chat.completions.create.return_value = response(cost=0.25)
            asyncio.run(gateway.chat(access, "openrouter:vendor/fast", [], "public"))
        r = self.client.get("/admin/models/usage?group_by=model", headers=self.admin)
        self.assertEqual(r.json()["rows"][0]["model"], "openrouter:vendor/fast")
        self.assertEqual(r.json()["rows"][0]["cost_usd"], 0.25)
        self.assertEqual(self.client.get("/admin/models/usage?group_by=secret", headers=self.admin).status_code, 422)
        self.assertEqual(self.client.get("/admin/models/usage?month=2026-13x", headers=self.admin).status_code, 422)

    def test_deleting_a_model(self):
        mid = self.model_row("vendor/plain")["id"]
        self.assertEqual(self.client.delete(f"/admin/models/{mid}", headers=self.admin).status_code, 204)
        self.assertEqual(self.client.get(f"/admin/models/{mid}", headers=self.admin).status_code, 404)


class SeedTest(unittest.TestCase):
    def test_0007_gives_the_admin_role_the_models_area(self):
        self.assertIn(("admin", "admin_area", "models", "admin"), seed_data()["permissions"])


if __name__ == "__main__":
    unittest.main()
