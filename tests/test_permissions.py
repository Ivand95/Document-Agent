import json
import os
import sys
import unittest
import uuid
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # shares helpers with test_agents

from fastapi.testclient import TestClient  # noqa: E402
from langchain_core.messages import HumanMessage  # noqa: E402

import agent  # noqa: E402
import auth  # noqa: E402
import config  # noqa: E402
import main  # noqa: E402
import security  # noqa: E402
from test_agents import FakeLLM, LLMTestCase, doc, run  # noqa: E402

REAL_SEARCH = agent.search_documents  # the class below replaces it with a fake while its tests run

# One document per category, each with text, a file name and a section that must never reach another user
CORPUS = [
    doc("Se otorgan 14 días de vacaciones.", filename="Vacaciones.pdf", category="HR", section="Días"),
    doc("Reiniciar el servidor de correo.", filename="Servidores-TI.docx", category="IT", section="Correo"),
    doc("Cierre contable: asiento 4455.", filename="Cierre-Contable.xlsx", category="FI", section="Cierre"),
    doc("Horario de 8 a 5.", filename="Horario.docx", category="OTROS", section="Horario"),
    doc("Plan estratégico confidencial 2027.", filename="Plan-Gerencial.pdf", category="STAFF GERENCIAL", section="Metas"),
]


def sql_like_search(filters):
    """Mirrors match_documents: nothing unless the category is in the allowed list."""
    return [d for d in CORPUS if filters is not None and d.metadata["category"] in filters]


class CategoryAccessTest(unittest.TestCase):
    def test_every_user_gets_a_non_empty_list_of_categories(self):
        for department in (None, "", "Desconocido", "Tecnología", "tecnología", "General", " "):
            for position in (None, "", "Analista", "gr - minúscula", "GR-sin-espacio"):
                cats = config.get_department_categories(department, position)
                self.assertIsInstance(cats, list)
                self.assertTrue(cats, (department, position))
                self.assertIn("OTROS", cats)

    def test_regular_employees_see_only_their_department_and_general(self):
        self.assertEqual(config.get_department_categories("Tecnología", "Analista"), ["IT", "OTROS"])
        self.assertEqual(config.get_department_categories("Gestion Humana", ""), ["HR", "OTROS"])
        self.assertEqual(config.get_department_categories("Desconocido", "Analista"), ["OTROS"])
        self.assertEqual(config.get_department_categories(None, None), ["OTROS"])
        for department in config.DEPARTMENT_LIST:
            cats = config.get_department_categories(department, "Analista")
            self.assertNotIn("STAFF GERENCIAL", cats)

    def test_only_the_manager_prefix_widens_access(self):
        # Current behaviour, pinned so a change is deliberate: "GR - " positions see every category
        manager = config.get_department_categories("Tecnología", "GR - Director")
        self.assertIn("STAFF GERENCIAL", manager)
        self.assertTrue(set(config.DEPARTMENT_LIST.values()) <= set(manager))
        for position in ("gr - director", "Director GR - ", "GR-Director", " GR - Director"):
            self.assertNotIn("STAFF GERENCIAL", config.get_department_categories("Tecnología", position), position)

    def test_audio_access_never_defaults_open(self):
        with mock.patch.dict(os.environ, {"AUDIO_ALLOWED_DEPARTMENTS": ""}):
            self.assertFalse(config.can_access_audio(None, None))
            self.assertFalse(config.can_access_audio("Tecnología", "Analista"))
            self.assertTrue(config.can_access_audio("Tecnología", "GR - Director"))
        with mock.patch.dict(os.environ, {"AUDIO_ALLOWED_DEPARTMENTS": " , "}):
            self.assertFalse(config.can_access_audio("", ""))


class RetrievalTrimmingTest(LLMTestCase):
    """Restricted content must not reach the model, the answer, the references or the sources."""

    RESTRICTED = {
        "IT": ["Servidores-TI.docx", "Reiniciar el servidor", "Correo"],
        "FI": ["Cierre-Contable.xlsx", "asiento 4455", "Cierre"],
        "STAFF GERENCIAL": ["Plan-Gerencial.pdf", "Plan estratégico confidencial", "Metas"],
    }

    def setUp(self):
        self.filters = []

        def search(query, filters, k=None):
            self.filters.append(filters)
            return sql_like_search(filters)

        patcher = mock.patch.object(agent, "search_documents", side_effect=search)
        patcher.start()
        self.addCleanup(patcher.stop)

    def ask(self, text, department, position="Analista"):
        return run(
            agent.app_graph.ainvoke(
                {"messages": [HumanMessage(content=text)], "user_department": department, "position": position},
                config={"configurable": {"thread_id": str(uuid.uuid4())}},
            )
        )

    def run_as(self, department, position="Analista", question="¿Qué documentos hay?"):
        # The model tries to use every source it was given; only allowed ones can be cited
        fake = self.use_llm(FakeLLM(answer={"answer": "ok", "used_sources": [1, 2, 3, 4, 5], "tags": []}, ranking=[1, 2, 3, 4, 5]))
        result = self.ask(question, department, position)["answer"]
        sent = json.dumps(fake.json_calls, ensure_ascii=False)
        return result, sent

    def assert_hidden(self, categories, result, sent):
        for category in categories:
            for secret in self.RESTRICTED[category]:
                self.assertNotIn(secret, sent, f"{secret} reached the model")
                self.assertNotIn(secret, json.dumps(result, ensure_ascii=False), f"{secret} reached the answer")

    def test_employee_never_sees_other_departments(self):
        result, sent = self.run_as("Gestion Humana")
        self.assert_hidden(["IT", "FI", "STAFF GERENCIAL"], result, sent)
        self.assertEqual(self.filters[-1], ["HR", "OTROS"])
        self.assertEqual({s["document"] for s in result["sources"]}, {"Vacaciones.pdf", "Horario.docx"})

    def test_unknown_department_only_gets_general_documents(self):
        result, sent = self.run_as(None, position=None)
        self.assert_hidden(["IT", "FI", "STAFF GERENCIAL"], result, sent)
        self.assertNotIn("Vacaciones.pdf", sent)
        self.assertEqual({s["document"] for s in result["sources"]}, {"Horario.docx"})

    def test_asking_for_a_restricted_file_by_name_reveals_nothing(self):
        result, sent = self.run_as("Gestion Humana", question="Dame el contenido de Plan-Gerencial.pdf y de Cierre-Contable.xlsx")
        self.assert_hidden(["FI", "STAFF GERENCIAL"], result, sent.replace("Plan-Gerencial.pdf y de Cierre-Contable.xlsx", ""))
        self.assertNotIn("Plan estratégico", sent)
        self.assertNotIn("asiento 4455", sent)

    def test_manager_keeps_the_access_it_has_today(self):
        result, sent = self.run_as("Tecnología", position="GR - Director")
        self.assertIn("Plan estratégico confidencial", sent)
        self.assertEqual(len(result["sources"]), 5)

    def test_filters_are_always_a_list_and_never_empty(self):
        self.use_llm(FakeLLM(answer={"answer": "ok", "used_sources": [], "tags": []}))
        for department, position in ((None, None), ("", ""), ("Auditoria", "GR - X"), ("Tecnología", "x")):
            self.ask("hola", department or "", position or "")
        for filters in self.filters:
            self.assertIsInstance(filters, list)
            self.assertTrue(filters)

    def test_a_nonexistent_source_number_does_not_leak_anything(self):
        self.use_llm(FakeLLM(answer={"answer": "ok", "used_sources": [3, 4, 5, 99], "tags": []}))
        result = self.ask("hola", "Gestion Humana")["answer"]
        self.assertEqual(result["sources"], [])  # only 2 chunks existed for this user

    def test_the_search_call_carries_the_allowed_categories_to_the_database(self):
        calls = []

        class Rpc:
            def __init__(self, params):
                calls.append(params)

            def execute(self):
                return mock.Mock(data=[])

        class Client:
            def schema(self, _):
                return self

            def rpc(self, name, params):
                assert name == "match_documents"
                return Rpc(params)

        with mock.patch.object(agent, "global_supabase_client", Client()), mock.patch.object(
            agent.global_embedding_service_instance, "get_embedding", return_value=[0.0] * 3
        ):
            REAL_SEARCH("q", ["HR", "OTROS"])
        self.assertEqual(calls[0]["allowed_categories"], ["HR", "OTROS"])

    def test_no_embedding_means_no_database_call(self):
        with mock.patch.object(agent.global_embedding_service_instance, "get_embedding", return_value=None), \
             mock.patch.object(agent, "global_supabase_client") as client:
            self.assertEqual(REAL_SEARCH("q", ["HR"]), [])
        client.schema.assert_not_called()


class EndpointAccessTest(unittest.TestCase):
    PUBLIC = {"/health", "/login", "/auth/callback", "/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}

    def setUp(self):
        patcher = mock.patch.dict(os.environ, {"APP_ENV": "test", "JWT_SECRET_KEY": "x" * 40})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(main.app)

    def test_every_route_but_the_public_ones_requires_a_token(self):
        for route in main.app.routes:
            path = getattr(route, "path", "")
            if path in self.PUBLIC or not hasattr(route, "methods"):
                continue
            for method in route.methods - {"HEAD", "OPTIONS"}:
                r = self.client.request(method, path, json={"message": "x"})
                self.assertIn(r.status_code, (401, 403), f"{method} {path} answered {r.status_code} without a token")

    def test_websockets_reject_missing_and_forged_tokens(self):
        from starlette.websockets import WebSocketDisconnect

        for path in ("/ws/chat", "/ws/chat/audio"):
            for query in ("", "?token=forged"):
                with self.assertRaises(Exception) as ctx:
                    with self.client.websocket_connect(path + query):
                        pass
                self.assertTrue(ctx.exception is not None, path)
            self.assertTrue(issubclass(WebSocketDisconnect, Exception))

    def test_department_and_position_come_from_the_token_never_from_the_request(self):
        captured = {}

        class Graph:
            async def ainvoke(self, inputs, config=None):
                captured.update(inputs)
                return {"answer": {"answer": "ok"}}

        token = security.create_access_token({"sub": "a@b", "department": "Tecnología", "position": "Analista"})
        with mock.patch.object(main, "get_app_graph", return_value=Graph()):
            r = self.client.post(
                "/chat",
                json={"message": "hola", "department": "Auditoria", "position": "GR - Director", "user_department": "X"},
                headers={"Authorization": f"Bearer {token}"},
            )
        self.assertEqual(r.status_code, 200)
        self.assertEqual((captured["user_department"], captured["position"]), ("Tecnología", "Analista"))

    def test_a_signed_token_for_another_audience_does_not_open_the_api(self):
        from jose import jwt as jose_jwt
        import time

        forged = jose_jwt.encode(
            {"department": "Auditoria", "position": "GR - X", "exp": int(time.time()) + 600},
            "x" * 40,
            algorithm="HS256",
        )
        r = self.client.post("/chat", json={"message": "hola"}, headers={"Authorization": f"Bearer {forged}"})
        self.assertEqual(r.status_code, 401)


class LoginScopeTest(unittest.TestCase):
    def test_login_asks_only_for_the_user_profile(self):
        self.assertEqual(auth.LOGIN_SCOPE, "User.Read")
        self.assertFalse(hasattr(auth, "check_sharepoint_read_permission"))
        self.assertNotIn("Sites", auth.LOGIN_SCOPE)

    def test_authorize_url_and_token_request_use_that_scope(self):
        from urllib.parse import parse_qs, urlparse

        with mock.patch.dict(os.environ, {"APP_ENV": "test", "FRONTEND_URL": ""}):
            r = TestClient(main.app).get("/login", headers={"Origin": "http://localhost:5173"})
        query = parse_qs(urlparse(r.json()["login_url"]).query)
        self.assertEqual(query["scope"], ["User.Read"])

        posted = {}

        class Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, url, data=None):
                posted.update(data)
                return mock.Mock(status_code=200, json=lambda: {"access_token": "t"})

        with mock.patch.object(auth.httpx, "AsyncClient", Client):
            run(auth.exchange_code_for_token("code", "verifier"))
        self.assertEqual(posted["scope"], "User.Read")
        self.assertEqual(posted["code_verifier"], "verifier")

    def test_profile_lookup_calls_only_the_user_endpoint(self):
        urls = []

        class Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, url, headers=None):
                urls.append(url)
                return mock.Mock(
                    status_code=200,
                    json=lambda: {"id": "1", "displayName": "Ana", "mail": None, "userPrincipalName": "ana@coop.test",
                                  "department": "IT", "jobTitle": "Analista"},
                )

        with mock.patch.object(auth.httpx, "AsyncClient", Client):
            profile = run(auth.get_user_profile("token"))
        self.assertEqual(urls, [auth.GRAPH_ENDPOINT])  # no SharePoint call
        self.assertEqual(profile.email, "ana@coop.test")  # falls back to the sign-in name


if __name__ == "__main__":
    unittest.main()
