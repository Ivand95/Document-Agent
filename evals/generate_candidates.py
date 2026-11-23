"""Drafts candidate golden questions from random stored chunks, for a human to review.

    uv run python evals/generate_candidates.py --n 30

Writes evals/candidates.jsonl. Nothing is trusted until you read each case, fix or delete it,
and move it into golden.jsonl: the LLM can write a wrong reference answer.
"""
import argparse
import asyncio
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "app"))

SCHEMA = {
    "type": "object",
    "properties": {
        "usable": {"type": "boolean"},
        "question": {"type": "string"},
        "reference_answer": {"type": "string"},
        "keywords": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["usable", "question", "reference_answer", "keywords"],
    "additionalProperties": False,
}

PROMPT = """A partir del FRAGMENTO, escribe una pregunta que un empleado haría y cuya respuesta esté \
completa y sin ambigüedad en el fragmento (no menciones "el fragmento"). Da la respuesta de referencia y de 1 a 3 \
palabras clave exactas (cifras o nombres) que deban aparecer en una buena respuesta. \
Si el fragmento no permite una buena pregunta (índices, tablas ilegibles, texto sin contenido), usable=false."""


def department_for(category):
    from config import DEPARTMENT_LIST

    return next((name for name, code in DEPARTMENT_LIST.items() if code == category), "General")


async def main():
    import llm
    from config import SUPABASE_SCHEMA, SUPABASE_TABLE, global_supabase_client

    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=30)
    args = parser.parse_args()

    table = global_supabase_client.schema(SUPABASE_SCHEMA).table(SUPABASE_TABLE)
    total = table.select("id", count="exact").limit(1).execute().count
    out = ROOT / "candidates.jsonl"
    written, seen = 0, set()

    with out.open("w") as f:
        for offset in random.sample(range(total), min(total, args.n * 3)):
            if written >= args.n:
                break
            row = table.select("content,metadata").range(offset, offset).execute().data[0]
            meta, content = row["metadata"], row["content"]
            if len(content) < 300 or meta.get("filename") in seen:
                continue
            data = await llm.chat_json(
                [{"role": "system", "content": PROMPT}, {"role": "user", "content": f"FRAGMENTO:\n{content}"}],
                "candidate", SCHEMA,
            )
            if not data["usable"]:
                continue
            seen.add(meta.get("filename"))
            case = {
                "id": f"cand-{written + 1:02d}",
                "question": data["question"],
                "department": department_for(meta.get("category")),
                "position": "",
                "expected_sources": [Path(meta["filename"]).stem],
                "keywords": data["keywords"],
                "reference_answer": data["reference_answer"],
                "needs_review": True,
            }
            f.write(json.dumps(case, ensure_ascii=False) + "\n")
            written += 1
    print(f"Wrote {written} candidates to {out}. Review them before moving any into golden.jsonl.")


if __name__ == "__main__":
    asyncio.run(main())
