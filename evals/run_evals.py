"""Runs the golden questions through the real document agent and scores the answers.

    uv run python evals/run_evals.py                      # all cases
    uv run python evals/run_evals.py --ids nomina-afiliacion,smalltalk
    uv run python evals/run_evals.py --label after-rerank --judge
    uv run python evals/run_evals.py --compare evals/results/<previous>.json

It calls OpenAI and Supabase (a few cheap requests per case). Case fields (JSONL):
  id, question | turns[], department, position
  expected_sources   filename substrings; retrieval must return at least one
  keywords           every keyword must appear in the answer (case/accents ignored)
  not_found          the agent must say it didn't find the information
  forbidden_sources  filename substrings that must NOT be retrieved (access control)
  no_citations       the answer must not cite documents
  reference_answer   only used by --judge
"""
import argparse
import asyncio
import json
import os
import sys
import time
import unicodedata
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "app"))

NOT_FOUND_MARKERS = ("no encontre", "no tengo informacion", "no hay informacion")


def normalize(text):
    text = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


def score_case(case, run):
    """`run` = {"answer": dict, "sources": [filenames retrieved]}. Returns {check: bool}."""
    checks = {}
    answer = run["answer"]
    text = normalize(answer.get("answer", ""))
    retrieved = [normalize(s) for s in run["sources"]]

    if case.get("expected_sources"):
        wanted = [normalize(s) for s in case["expected_sources"]]
        checks["retrieval"] = any(w in r for w in wanted for r in retrieved)
        cited = normalize(answer.get("document_reference", ""))
        checks["citation"] = any(w in cited for w in wanted)
    if case.get("keywords"):
        checks["keywords"] = all(normalize(k) in text for k in case["keywords"])
    if case.get("not_found"):
        checks["not_found"] = any(m in text for m in NOT_FOUND_MARKERS)
        checks["no_citations"] = not answer.get("document_reference")
    if case.get("no_citations"):
        checks["no_citations"] = not answer.get("document_reference")
    if case.get("forbidden_sources"):
        forbidden = [normalize(s) for s in case["forbidden_sources"]]
        checks["access"] = not any(f in r for f in forbidden for r in retrieved)
    if "judge" in run:
        checks["judge"] = run["judge"]
    return checks


def load_cases(path, ids=None):
    cases = [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
    return [c for c in cases if not ids or c["id"] in ids]


async def judge(question, reference, answer):
    import llm

    schema = {
        "type": "object",
        "properties": {"correct": {"type": "boolean"}, "reason": {"type": "string"}},
        "required": ["correct", "reason"],
        "additionalProperties": False,
    }
    prompt = [
        {
            "role": "system",
            "content": "Eres un evaluador estricto. Indica si la RESPUESTA contiene la misma información "
            "esencial que la REFERENCIA (cifras y nombres exactos). Ignora diferencias de estilo.",
        },
        {"role": "user", "content": f"PREGUNTA: {question}\nREFERENCIA: {reference}\nRESPUESTA: {answer}"},
    ]
    return (await llm.chat_json(prompt, "judge", schema, model=llm.CHAT_MODEL))["correct"]


async def run_case(graph, case, use_judge):
    from langchain_core.messages import HumanMessage

    turns = case.get("turns") or [case["question"]]
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    started = time.time()
    for question in turns:
        result = await graph.ainvoke(
            {
                "messages": [HumanMessage(content=question)],
                "user_department": case["department"],
                "position": case.get("position", ""),
            },
            config=config,
        )
    run = {
        "answer": result["answer"],
        "sources": [d.metadata.get("filename", "") for d in result.get("context", [])],
        "seconds": round(time.time() - started, 1),
    }
    if use_judge and case.get("reference_answer"):
        run["judge"] = await judge(turns[-1], case["reference_answer"], run["answer"].get("answer", ""))
    return run


def snapshot_config():
    import config

    keys = ["CHAT_MODEL", "LIGHT_MODEL", "RETRIEVAL_K", "RETRIEVAL_CANDIDATES",
            "RETRIEVAL_MATCH_THRESHOLD", "RERANK_ENABLED", "HISTORY_MAX_MESSAGES"]
    return {k: getattr(config, k) for k in keys}


def print_report(results):
    width = max(len(r["id"]) for r in results)
    for r in results:
        marks = " ".join(f"{k}={'ok' if v else 'FAIL'}" for k, v in r["checks"].items())
        print(f"{'PASS' if r['passed'] else 'FAIL'}  {r['id']:<{width}}  {r['seconds']:>5}s  {marks}")
    passed = sum(r["passed"] for r in results)
    print(f"\n{passed}/{len(results)} passed")
    per_check = {}
    for r in results:
        for k, v in r["checks"].items():
            per_check.setdefault(k, []).append(v)
    print("  ".join(f"{k}: {sum(v)}/{len(v)}" for k, v in sorted(per_check.items())))


def print_comparison(results, previous_path):
    previous = {r["id"]: r for r in json.loads(Path(previous_path).read_text())["results"]}
    print(f"\nvs {previous_path}")
    changed = False
    for r in results:
        before = previous.get(r["id"])
        if before and before["passed"] != r["passed"]:
            changed = True
            print(f"  {'FIXED ' if r['passed'] else 'BROKEN'} {r['id']}")
    if not changed:
        print("  no case changed status")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", default=str(ROOT / "golden.jsonl"))
    parser.add_argument("--ids", help="comma separated case ids")
    parser.add_argument("--label", default="run")
    parser.add_argument("--judge", action="store_true", help="LLM-check answers against reference_answer")
    parser.add_argument("--compare", help="previous results file")
    args = parser.parse_args()

    from agent import app_graph

    cases = load_cases(args.cases, set(args.ids.split(",")) if args.ids else None)
    results = []
    for case in cases:
        try:
            run = await run_case(app_graph, case, args.judge)
            checks = score_case(case, run)
            results.append({"id": case["id"], "checks": checks, "passed": all(checks.values()),
                            "seconds": run["seconds"], "sources": run["sources"],
                            "answer": run["answer"].get("answer", "")})
        except Exception as e:
            results.append({"id": case["id"], "checks": {"error": False}, "passed": False,
                            "seconds": 0, "sources": [], "answer": f"ERROR: {e}"})

    print_report(results)
    if args.compare:
        print_comparison(results, args.compare)

    out_dir = ROOT / "results"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"{datetime.now():%Y%m%d-%H%M%S}_{args.label}.json"
    out.write_text(json.dumps({"config": snapshot_config(), "results": results}, ensure_ascii=False, indent=2))
    print(f"\nSaved {out}")


if __name__ == "__main__":
    asyncio.run(main())
