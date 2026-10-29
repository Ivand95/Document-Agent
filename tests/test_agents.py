import asyncio
import os
import sys
import unittest
import uuid
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

from langchain_core.documents import Document  # noqa: E402
from langchain_core.messages import AIMessage, HumanMessage  # noqa: E402

import agent  # noqa: E402
import audio_agent  # noqa: E402
import config  # noqa: E402
import llm  # noqa: E402
import rag  # noqa: E402


def doc(text, **meta):
    return Document(page_content=text, metadata=meta)


def run(coro):
    return asyncio.run(coro)


class FakeLLM:
    """Stands in for llm.chat_json_sync / chat_text_sync and records every call."""

    def __init__(self, answer=None, ranking=None, rewrite="pregunta reescrita", fail=False):
        self.answer, self.ranking, self.rewrite, self.fail = answer, ranking, rewrite, fail
        self.json_calls, self.text_calls = [], []

    def json(self, messages, name, schema, model=None, retries=1):
        self.json_calls.append((name, messages))
        if self.fail:
            raise ValueError("bad json")
        if name == "rerank":
            return {"ranking": self.ranking}
        return self.answer

    def text(self, messages, model=None):
        self.text_calls.append(messages)
        return self.rewrite

    def patch(self):
        stack = mock.patch.multiple(llm, chat_json_sync=self.json, chat_text_sync=self.text)
        stack.start()
        return stack


class LLMTestCase(unittest.TestCase):
    def use_llm(self, fake):
        patcher = fake.patch()
        self.addCleanup(patcher.stop)
        return fake


class RagTest(LLMTestCase):
    def test_trim_history_keeps_the_last_messages(self):
        messages = [HumanMessage(content=str(i)) for i in range(20)]
        with mock.patch.object(rag, "HISTORY_MAX_MESSAGES", 5):
            trimmed = rag.trim_history(messages)
        self.assertEqual([m.content for m in trimmed], ["15", "16", "17", "18", "19"])

    def test_rewrite_skips_the_llm_without_history(self):
        fake = self.use_llm(FakeLLM())
        self.assertEqual(run(rag.rewrite_question([HumanMessage(content="hola")])), "hola")
        self.assertEqual(fake.text_calls, [])

    def test_rewrite_uses_history_and_falls_back_on_error(self):
        history = [
            HumanMessage(content="¿Cuántos días de vacaciones hay?"),
            AIMessage(content="14 días."),
            HumanMessage(content="¿y si tengo más de 5 años?"),
        ]
        fake = self.use_llm(FakeLLM(rewrite="¿Cuántos días de vacaciones con más de 5 años?"))
        self.assertEqual(run(rag.rewrite_question(history)), "¿Cuántos días de vacaciones con más de 5 años?")
        prompt = fake.text_calls[0][1]["content"]
        self.assertIn("14 días.", prompt)
        self.assertIn("¿y si tengo más de 5 años?", prompt)

        with mock.patch.object(llm, "chat_text_sync", side_effect=RuntimeError("down")):
            self.assertEqual(run(rag.rewrite_question(history)), "¿y si tengo más de 5 años?")

    def test_rerank_orders_dedupes_and_never_loses_candidates(self):
        docs = [doc(f"d{i}", filename=f"f{i}") for i in range(5)]
        self.use_llm(FakeLLM(ranking=[3, 3, 99, 1]))
        with mock.patch.object(rag, "RERANK_ENABLED", True):
            top = rag.rerank("q", docs, 3)
        self.assertEqual([d.page_content for d in top], ["d3", "d1", "d0"])

    def test_rerank_falls_back_to_vector_order_and_skips_when_not_needed(self):
        docs = [doc(f"d{i}") for i in range(5)]
        with mock.patch.object(rag, "RERANK_ENABLED", True):
            with mock.patch.object(llm, "chat_json_sync", side_effect=RuntimeError("down")):
                self.assertEqual(rag.rerank("q", docs, 2), docs[:2])
            fake = self.use_llm(FakeLLM(ranking=[0]))
            self.assertEqual(rag.rerank("q", docs[:2], 2), docs[:2])  # nothing to cut
            self.assertEqual(fake.json_calls, [])
        with mock.patch.object(rag, "RERANK_ENABLED", False):
            self.assertEqual(rag.rerank("q", docs, 2), docs[:2])


class DocumentAgentTest(LLMTestCase):
    def setUp(self):
        self.docs = [
            doc("Se otorgan 14 días.", filename="Vacaciones.pdf", category="HR", section="Días", page_no=2),
            doc("Solicitar con 15 días.", filename="Vacaciones.pdf", category="HR", section="Solicitud", page_no=3),
            doc("Horario 8 a 5.", filename="Horario.docx", category="OTROS"),
        ]
        self.search = mock.patch.object(agent, "search_documents", return_value=self.docs)
        self.search_mock = self.search.start()
        self.addCleanup(self.search.stop)

    def ask(self, text, thread=None, department="Gestion Humana", position=""):
        thread = thread or str(uuid.uuid4())
        return run(
            agent.app_graph.ainvoke(
                {"messages": [HumanMessage(content=text)], "user_department": department, "position": position},
                config={"configurable": {"thread_id": thread}},
            )
        )

    def test_references_are_built_from_metadata_of_used_sources(self):
        self.use_llm(FakeLLM(answer={"answer": "14 días", "used_sources": [2, 1, 2], "tags": ["vacaciones"]}))
        result = self.ask("¿Cuántos días de vacaciones?")["answer"]

        self.assertEqual(result["answer"], "14 días")
        self.assertEqual(result["document_reference"], "Vacaciones.pdf")
        self.assertEqual(result["department_reference"], "Gestión Humana")
        self.assertEqual(result["section_reference"], "Solicitud (p. 3); Días (p. 2)")
        self.assertEqual(len(result["sources"]), 2)

    def test_no_used_sources_means_no_references_but_keys_exist_for_the_ui(self):
        self.use_llm(FakeLLM(answer={"answer": "Hola", "used_sources": [], "tags": []}))
        result = self.ask("hola")["answer"]
        for key in ("document_reference", "department_reference", "section_reference"):
            self.assertEqual(result[key], "")

    def test_out_of_range_sources_are_ignored(self):
        self.use_llm(FakeLLM(answer={"answer": "x", "used_sources": [0, 7, -1], "tags": []}))
        self.assertEqual(self.ask("q")["answer"]["sources"], [])

    def test_sources_with_metadata_reach_the_prompt(self):
        fake = self.use_llm(FakeLLM(answer={"answer": "x", "used_sources": [], "tags": []}))
        self.ask("q")
        system = fake.json_calls[-1][1][0]["content"]
        self.assertIn("[Fuente 1] Documento: Vacaciones.pdf | Departamento: Gestión Humana | Sección: Días | Página: 2", system)
        self.assertIn("Se otorgan 14 días.", system)

    def test_no_context_is_flagged_in_the_prompt(self):
        self.search_mock.return_value = []
        fake = self.use_llm(FakeLLM(answer={"answer": "x", "used_sources": [], "tags": []}))
        self.ask("q")
        self.assertIn("No se encontraron fragmentos relevantes", fake.json_calls[-1][1][0]["content"])

    def test_department_filter_comes_from_department_and_position(self):
        self.use_llm(FakeLLM(answer={"answer": "x", "used_sources": [], "tags": []}))
        self.ask("q", department="Tecnología")
        self.assertEqual(self.search_mock.call_args.args[1], ["IT", "OTROS"])
        self.ask("q", department="Tecnología", position="GR - Director")
        self.assertIn("STAFF GERENCIAL", self.search_mock.call_args.args[1])

    def test_follow_up_is_rewritten_and_history_stores_plain_answers(self):
        fake = self.use_llm(FakeLLM(answer={"answer": "14 días", "used_sources": [1], "tags": []}, rewrite="vacaciones más de 5 años"))
        thread = str(uuid.uuid4())
        self.ask("¿Cuántos días de vacaciones?", thread)
        self.assertEqual(fake.text_calls, [])  # first turn: nothing to rewrite

        self.ask("¿y con más de 5 años?", thread)
        self.assertEqual(self.search_mock.call_args.args[0], "vacaciones más de 5 años")

        sent = fake.json_calls[-1][1]
        self.assertEqual([m["role"] for m in sent], ["system", "user", "assistant", "user"])
        self.assertEqual(sent[2]["content"], "14 días")  # not the JSON envelope

    def test_llm_failure_returns_a_friendly_message_not_a_json_error(self):
        self.use_llm(FakeLLM(fail=True))
        result = self.ask("q")["answer"]
        self.assertEqual(result["answer"], agent.FALLBACK_ERROR)
        self.assertNotIn("error", result)
        self.assertEqual(result["document_reference"], "")


class AudioFiltersTest(unittest.TestCase):
    def test_year_is_not_an_extension(self):
        f = audio_agent.extract_filters("llamadas de mayo 2026")
        self.assertIsNone(f["extension"])

    def test_iso_date_digits_are_not_an_extension(self):
        f = audio_agent.extract_filters("resumen del 2026-05-08")
        self.assertEqual((f["date"], f["extension"]), ("2026-05-08", None))

    def test_dmy_date_is_normalized(self):
        self.assertEqual(audio_agent.extract_filters("el 08/05/2026")["date"], "2026-05-08")
        self.assertIsNone(audio_agent.extract_filters("el 31/02/2026")["date"])

    def test_extension_explicit_or_bare(self):
        self.assertEqual(audio_agent.extract_filters("extensión 1055")["extension"], "1055")
        self.assertEqual(audio_agent.extract_filters("Ext. 345")["extension"], "345")
        self.assertEqual(audio_agent.extract_filters("dame un resumen de 1055")["extension"], "1055")

    def test_phone_number(self):
        f = audio_agent.extract_filters("llamadas al 18097695980 el 2026-05-12")
        self.assertEqual((f["recipient"], f["date"], f["extension"]), ("18097695980", "2026-05-12", None))


class AudioAgentTest(LLMTestCase):
    def setUp(self):
        meta = dict(
            filename="[Ana]_1034-18097695980_20260508.wav", employee_name="Ana", extension="1034",
            date_formatted="2026-05-08", phone_number="18097695980", sentiment_score=4,
            call_purpose="Queja", resolution_status="Pendiente", summary="Cliente molesto por cobro.",
            recommendation="Dar seguimiento", start="00:00", end="01:00",
        )
        self.docs = [doc("Hola, llamo por un cobro.", **meta), doc("Lo reviso.", **meta)]
        patcher = mock.patch.object(audio_agent, "search_conversations", return_value=self.docs)
        self.search = patcher.start()
        self.addCleanup(patcher.stop)

    def ask(self, text, thread=None):
        return run(
            audio_agent.app_audio_graph.ainvoke(
                {"messages": [HumanMessage(content=text)], "user_department": "Auditoria", "position": ""},
                config={"configurable": {"thread_id": thread or str(uuid.uuid4())}},
            )
        )

    def test_graph_runs_and_call_data_comes_from_metadata(self):
        self.use_llm(FakeLLM(answer={"answer": "Fue una queja.", "used_sources": [1, 2], "action_items": ["Revisar cobro"], "tags": ["queja"]}))
        result = self.ask("resumen de la extensión 1034 el 2026-05-08")["answer"]

        self.assertEqual(self.search.call_args.args[1], {"extension": "1034", "date": "2026-05-08", "recipient": None})
        self.assertEqual(result["employee_name"], "Ana")
        self.assertEqual(result["extension"], "1034")
        self.assertEqual(result["conversation_date"], "2026-05-08")
        self.assertEqual(result["summary"], "Cliente molesto por cobro.")
        self.assertEqual(result["sentiment_score"], 4)
        self.assertEqual(result["action_items"], ["Revisar cobro"])
        self.assertEqual(len(result["calls"]), 1)  # two chunks, one call

    def test_call_header_carries_metrics_to_the_prompt(self):
        fake = self.use_llm(FakeLLM(answer={"answer": "x", "used_sources": [], "action_items": [], "tags": []}))
        self.ask("q")
        system = fake.json_calls[-1][1][0]["content"]
        self.assertIn("Sentimiento: 4/10", system)
        self.assertIn("Tramo: 00:00-01:00", system)

    def test_failure_returns_friendly_answer_with_ui_keys(self):
        self.use_llm(FakeLLM(fail=True))
        result = self.ask("q")["answer"]
        self.assertEqual(result["answer"], audio_agent.FALLBACK_ERROR)
        for key in ("employee_name", "conversation_date", "extension", "summary", "tags"):
            self.assertIn(key, result)


class AudioAccessTest(unittest.TestCase):
    def check(self, department, position, allowed=None):
        env = {} if allowed is None else {"AUDIO_ALLOWED_DEPARTMENTS": allowed}
        with mock.patch.dict(os.environ, env):
            if allowed is None:
                os.environ.pop("AUDIO_ALLOWED_DEPARTMENTS", None)
            return config.can_access_audio(department, position)

    def test_default_is_managers_only(self):
        self.assertTrue(self.check("Tecnología", "GR - Director"))
        self.assertFalse(self.check("Tecnología", "Analista"))
        self.assertFalse(self.check("Auditoria", ""))

    def test_allowed_departments_from_env(self):
        self.assertTrue(self.check("Auditoria", "Analista", "Auditoria, Cumplimiento"))
        self.assertTrue(self.check("Cumplimiento", "", "Auditoria, Cumplimiento"))
        self.assertFalse(self.check("Marketing", "", "Auditoria, Cumplimiento"))


if __name__ == "__main__":
    unittest.main()
