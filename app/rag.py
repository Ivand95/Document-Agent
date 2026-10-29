import logging

from langchain_core.messages import AIMessage, HumanMessage

import llm
from config import HISTORY_MAX_MESSAGES, RERANK_ENABLED

log = logging.getLogger("rag")

SNIPPET_CHARS = 500

REWRITE_PROMPT = """Reescribe la última pregunta del usuario como una pregunta completa e independiente, \
en español, usando la conversación previa solo para resolver referencias ("eso", "y el otro", "¿cuánto cuesta?"). \
Conserva nombres, números, fechas y extensiones tal cual. Si la pregunta ya es independiente, devuélvela igual. \
Responde únicamente con la pregunta reescrita."""

RERANK_PROMPT = """Ordena los fragmentos por relevancia para responder la pregunta, del más al menos relevante. \
Descarta al final los que no ayudan. Responde con los índices en el campo "ranking"."""

RERANK_SCHEMA = {
    "type": "object",
    "properties": {"ranking": {"type": "array", "items": {"type": "integer"}}},
    "required": ["ranking"],
    "additionalProperties": False,
}


def trim_history(messages):
    """Last HISTORY_MAX_MESSAGES messages; the current question is always the last one."""
    return list(messages[-HISTORY_MAX_MESSAGES:])


def to_openai_messages(messages):
    return [
        {"role": "user" if isinstance(m, HumanMessage) else "assistant", "content": m.content}
        for m in messages
    ]


async def rewrite_question(messages) -> str:
    """Standalone version of the last question, so follow-ups retrieve the right chunks."""
    question = messages[-1].content
    history = trim_history(messages[:-1])
    if not history:
        return question

    transcript = "\n".join(
        f"{'Usuario' if isinstance(m, HumanMessage) else 'Asistente'}: {m.content}"
        for m in history
    )
    prompt = [
        {"role": "system", "content": REWRITE_PROMPT},
        {"role": "user", "content": f"Conversación previa:\n{transcript}\n\nÚltima pregunta: {question}"},
    ]
    try:
        return await llm.chat_text(prompt) or question
    except Exception:
        log.exception("Query rewrite failed; using the original question")
        return question


def rerank(query, docs, top_n):
    """Keeps the top_n most relevant docs. Falls back to vector order if the LLM call fails."""
    if not RERANK_ENABLED or len(docs) <= top_n:
        return docs[:top_n]

    listing = "\n\n".join(
        f"[{i}] ({d.metadata.get('filename', '?')}) {d.page_content[:SNIPPET_CHARS]}"
        for i, d in enumerate(docs)
    )
    prompt = [
        {"role": "system", "content": RERANK_PROMPT},
        {"role": "user", "content": f"Pregunta: {query}\n\nFragmentos:\n{listing}"},
    ]
    try:
        ranking = llm.chat_json_sync(prompt, "rerank", RERANK_SCHEMA, model=llm.LIGHT_MODEL)["ranking"]
    except Exception:
        log.exception("Rerank failed; using vector order")
        return docs[:top_n]

    order = []
    for i in ranking:
        if isinstance(i, int) and 0 <= i < len(docs) and i not in order:
            order.append(i)
    order += [i for i in range(len(docs)) if i not in order]  # never lose candidates
    return [docs[i] for i in order[:top_n]]


def answer_message(text) -> AIMessage:
    """History stores the plain answer, not the JSON envelope."""
    return AIMessage(content=text)


async def run_cli(graph, department="General", position=""):
    """Interactive loop against a compiled graph, for local debugging."""
    import json
    import os
    import uuid

    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    department = os.getenv("CLI_DEPARTMENT", department)
    position = os.getenv("CLI_POSITION", position)
    print("Agent ready. Type 'exit' to stop.")
    while True:
        text = input("\nYou: ").strip()
        if text.lower() in ("exit", "quit", "bye"):
            break
        if not text:
            continue
        result = await graph.ainvoke(
            {
                "messages": [HumanMessage(content=text)],
                "user_department": department,
                "position": position,
            },
            config=config,
        )
        print(json.dumps(result["answer"], indent=2, ensure_ascii=False))
