import asyncio
import logging
import operator
import re
import sys
from datetime import datetime
from typing import Annotated, List
from typing_extensions import TypedDict

from langchain_core.documents import Document
from langchain_core.messages import BaseMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import StateGraph, END

import llm
import rag
from config import (
    AUDIO_MATCH_THRESHOLD,
    RETRIEVAL_CANDIDATES,
    RETRIEVAL_K,
    SUPABASE_SCHEMA,
    global_embedding_service_instance,
    global_supabase_client,
)

log = logging.getLogger("audio_agent")

SYSTEM_PROMPT = """Eres un analista de calidad de servicio de Cooperativa Barcelona. \
Respondes preguntas sobre llamadas telefónicas usando SOLO las transcripciones de LLAMADAS.

Reglas:
1. Responde siempre en español, en Markdown, con tono profesional y cordial.
2. Las transcripciones no traen etiquetas de quién habla: infiere por contexto quién es el Empleado (ofrece ayuda \
o servicios) y quién es el Cliente (solicita ayuda o información).
3. Cita datos exactos: fechas, extensiones, nombres y momentos de la llamada con el formato [MM:SS] cuando existan.
4. Las métricas (sentimiento, propósito, resolución) están en el encabezado de cada llamada: úsalas tal cual y no \
inventes otras. Para varias llamadas, compáralas o agrégalas indicando de cuáles hablas.
5. Si LLAMADAS no contiene la información, responde "No encontré esa información en las conversaciones disponibles." \
y no inventes. Si hay información parcial, preséntala indicando que es parcial.
6. Si el mensaje es un saludo o charla casual, responde de forma breve y amable, sin citar llamadas.
7. En "used_sources" lista los números de las [Llamada N] que realmente usaste (vacío si ninguna).
8. En "action_items" lista las tareas pendientes mencionadas en esas llamadas (vacío si no hay).
9. En "tags" incluye de 2 a 5 palabras clave del tema."""

ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "used_sources": {"type": "array", "items": {"type": "integer"}},
        "action_items": {"type": "array", "items": {"type": "string"}},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["answer", "used_sources", "action_items", "tags"],
    "additionalProperties": False,
}

FALLBACK_ERROR = "Lo siento, tuve un problema al generar la respuesta. Por favor intenta de nuevo."

ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
DMY_DATE = re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b")  # dd/mm/yyyy
EXPLICIT_EXT = re.compile(r"\bext(?:ensi[oó]n)?\.?\s*#?\s*(\d{3,4})\b", re.IGNORECASE)
BARE_EXT = re.compile(r"\b(\d{4})\b")
PHONE = re.compile(r"\b\d{7,15}\b")


# --- Define State ---
class AgentState(TypedDict):
    position: str
    user_department: str
    messages: Annotated[List[BaseMessage], operator.add]
    search_query: str
    context: List[Document]
    answer: dict


def extract_filters(text):
    """Extension / date / recipient mentioned in the question, to narrow the search."""
    date = None
    for pattern, order in ((ISO_DATE, (0, 1, 2)), (DMY_DATE, (2, 1, 0))):
        match = pattern.search(text)
        if match:
            y, m, d = (match.group(i + 1) for i in order)
            try:
                date = datetime(int(y), int(m), int(d)).strftime("%Y-%m-%d")
            except ValueError:
                continue
            text = text.replace(match.group(0), " ")  # its digits are not an extension/phone
            break

    extension = None
    explicit = EXPLICIT_EXT.search(text)
    if explicit:
        extension = explicit.group(1)
    else:
        # A bare 4-digit number is an extension unless it reads as a year
        for match in BARE_EXT.finditer(text):
            if not 1990 <= int(match.group(1)) <= 2100:
                extension = match.group(1)
                break

    recipient = PHONE.search(text)
    return {
        "extension": extension,
        "date": date,
        "recipient": recipient.group(0) if recipient else None,
    }


# --- Retrieval ---
def search_conversations(query_text, filters, k=RETRIEVAL_CANDIDATES):
    query_vector = global_embedding_service_instance.get_embedding(query_text)
    if query_vector is None:
        log.error("Failed to generate the query embedding")
        return []

    rpc_params = {
        "query_embedding": query_vector,
        "match_threshold": AUDIO_MATCH_THRESHOLD,
        "match_count": k,
        "filter_extension": filters["extension"],
        "filter_date": filters["date"],
        "filter_recipient": filters["recipient"],
    }
    try:
        response = (
            global_supabase_client.schema(SUPABASE_SCHEMA)
            .rpc("match_conversations", rpc_params)
            .execute()
        )
    except Exception:
        log.exception("Supabase search failed")
        return []

    conversations = []
    for record in response.data:
        metadata = dict(record.get("metadata") or {})
        if "similarity" in record:
            metadata["similarity"] = record["similarity"]
        conversations.append(
            Document(page_content=record.get("content", ""), metadata=metadata)
        )
    return conversations


def format_calls(docs):
    parts = []
    for i, doc in enumerate(docs, 1):
        m = doc.metadata
        header = [
            f"Empleado: {m.get('employee_name', 'N/D')}",
            f"Ext: {m.get('extension', 'N/D')}",
            f"Fecha: {m.get('date_formatted', 'N/D')}",
        ]
        if m.get("phone_number"):
            header.append(f"Teléfono: {m['phone_number']}")
        if m.get("start") and m.get("end"):
            header.append(f"Tramo: {m['start']}-{m['end']}")
        if m.get("call_purpose"):
            header.append(f"Propósito: {m['call_purpose']}")
        if m.get("sentiment_score") is not None:
            header.append(f"Sentimiento: {m['sentiment_score']}/10")
        if m.get("resolution_status"):
            header.append(f"Resolución: {m['resolution_status']}")
        parts.append(f"[Llamada {i}] " + " | ".join(header) + f"\nTranscripción:\n{doc.page_content}")
    return "\n\n".join(parts)


def build_call_details(docs, used_sources, action_items, tags, text):
    """Call data comes from the stored metadata, never from the LLM's memory."""
    picked = [
        docs[i - 1]
        for i in dict.fromkeys(used_sources)
        if isinstance(i, int) and 1 <= i <= len(docs)
    ]
    calls, seen = [], set()
    for doc in picked:
        m = doc.metadata
        key = m.get("filename") or id(doc)
        if key in seen:
            continue
        seen.add(key)
        calls.append(
            {
                "filename": m.get("filename"),
                "employee_name": m.get("employee_name"),
                "extension": m.get("extension"),
                "conversation_date": m.get("date_formatted"),
                "phone_number": m.get("phone_number"),
                "sentiment_score": m.get("sentiment_score"),
                "call_purpose": m.get("call_purpose"),
                "resolution_status": m.get("resolution_status"),
                "summary": m.get("summary"),
                "recommendation": m.get("recommendation"),
            }
        )

    def joined(field):
        return ", ".join(dict.fromkeys(str(c[field]) for c in calls if c[field]))

    primary = calls[0] if calls else {}
    return {
        "answer": text,
        "employee_name": joined("employee_name"),
        "extension": joined("extension"),
        "conversation_date": joined("conversation_date"),
        "summary": primary.get("summary") or "",
        "sentiment_score": primary.get("sentiment_score"),
        "call_purpose": primary.get("call_purpose"),
        "resolution_status": primary.get("resolution_status"),
        "recommendation": primary.get("recommendation"),
        "action_items": action_items,
        "tags": tags,
        "calls": calls,
    }


# --- Nodes ---
async def rewrite_query(state: AgentState):
    return {"search_query": await rag.rewrite_question(state["messages"])}


def retrieve_conversations(state: AgentState):
    query = state.get("search_query") or state["messages"][-1].content

    candidates = search_conversations(query, extract_filters(query))
    docs = rag.rerank(query, candidates, RETRIEVAL_K)
    log.info("Retrieved %d candidates, kept %d", len(candidates), len(docs))
    return {"context": docs}


async def generate_answer(state: AgentState):
    docs = state.get("context", [])
    calls = format_calls(docs) or "(No se encontraron llamadas relevantes.)"
    messages = [{"role": "system", "content": f"{SYSTEM_PROMPT}\n\nLLAMADAS:\n{calls}"}]
    messages += rag.to_openai_messages(rag.trim_history(state["messages"]))

    try:
        data = await llm.chat_json(messages, "audio_answer", ANSWER_SCHEMA)
    except Exception:
        log.exception("Answer generation failed")
        answer = build_call_details([], [], [], [], FALLBACK_ERROR)
        return {"answer": answer, "messages": [rag.answer_message(FALLBACK_ERROR)]}

    answer = build_call_details(
        docs, data["used_sources"], data["action_items"], data["tags"], data["answer"]
    )
    return {"answer": answer, "messages": [rag.answer_message(data["answer"])]}


# --- Graph Construction ---
workflow = StateGraph(AgentState)
workflow.add_node("rewrite", rewrite_query)
workflow.add_node("retrieve", retrieve_conversations)
workflow.add_node("generate", generate_answer)
workflow.set_entry_point("rewrite")
workflow.add_edge("rewrite", "retrieve")
workflow.add_edge("retrieve", "generate")
workflow.add_edge("generate", END)

memory = MemorySaver()

app_audio_graph = workflow.compile(checkpointer=memory)

# --- Main Execution ---
if __name__ == "__main__":
    try:
        asyncio.run(rag.run_cli(app_audio_graph))
    except KeyboardInterrupt:
        sys.exit(0)
