import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "evals"))

import run_evals  # noqa: E402


def run(answer="", doc_ref="", sources=()):
    return {"answer": {"answer": answer, "document_reference": doc_ref}, "sources": list(sources)}


class ScoringTest(unittest.TestCase):
    def test_golden_file_is_valid(self):
        cases = run_evals.load_cases(Path(run_evals.ROOT) / "golden.jsonl")
        ids = [c["id"] for c in cases]
        self.assertEqual(len(ids), len(set(ids)))
        for c in cases:
            self.assertTrue(c.get("question") or c.get("turns"), c["id"])
            self.assertTrue(c.get("department"), c["id"])
            self.assertTrue(
                any(k in c for k in ("expected_sources", "keywords", "not_found", "forbidden_sources")), c["id"]
            )

    def test_keywords_ignore_case_and_accents(self):
        case = {"keywords": ["Consejo de Administración", "30%"]}
        ok = run("El CONSEJO DE ADMINISTRACION aprueba hasta el 30% ")
        self.assertEqual(run_evals.score_case(case, ok), {"keywords": True})
        bad = run("El Consejo de Administración")
        self.assertEqual(run_evals.score_case(case, bad), {"keywords": False})

    def test_retrieval_and_citation(self):
        case = {"expected_sources": ["Nomina ágil"]}
        good = run("x", "Nomina ágil preguntas (3).pdf", ["Nomina ágil preguntas (3).pdf"])
        self.assertEqual(run_evals.score_case(case, good), {"retrieval": True, "citation": True})
        miss = run("x", "", ["Otro.pdf"])
        self.assertEqual(run_evals.score_case(case, miss), {"retrieval": False, "citation": False})

    def test_not_found_requires_admission_and_no_citations(self):
        case = {"not_found": True}
        good = run("No encontré esa información en los documentos disponibles.")
        self.assertEqual(run_evals.score_case(case, good), {"not_found": True, "no_citations": True})
        invented = run("La contraseña es 1234", "Wifi.pdf")
        self.assertEqual(run_evals.score_case(case, invented), {"not_found": False, "no_citations": False})

    def test_forbidden_sources_detect_access_leaks(self):
        case = {"forbidden_sources": ["régimen de consecuencias"]}
        self.assertEqual(run_evals.score_case(case, run(sources=["Otro.pdf"])), {"access": True})
        leak = run(sources=["Propuesta de Régimen de Consecuencias.docx"])
        self.assertEqual(run_evals.score_case(case, leak), {"access": False})


if __name__ == "__main__":
    unittest.main()
