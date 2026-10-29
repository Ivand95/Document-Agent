import asyncio
import logging
import operator
import sys
from typing import Annotated, List
from typing_extensions import TypedDict

from langchain_core.documents import Document
from langchain_core.messages import BaseMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import StateGraph, END

import llm
import rag
from config import (
    CATEGORY_LABELS,
    RETRIEVAL_CANDIDATES,
    RETRIEVAL_K,
    RETRIEVAL_MATCH_THRESHOLD,
    SUPABASE_SCHEMA,
    get_department_categories,
    global_embedding_service_instance,
    global_supabase_client,
)

log = logging.getLogger("agent")

SYSTEM_PROMPT = """Eres el asistente interno de Cooperativa Barcelona (CoopBarcelona). \
Ayudas a los empleados respondiendo con base en los fragmentos de documentos internos que aparecen en FUENTES.

Reglas:
1. Responde siempre en español, en Markdown, con tono profesional y cordial.
2. Si el mensaje es un saludo o charla casual, responde de forma breve y amable, sin citar fuentes.
3. Si la pregunta es sobre la cooperativa, sus políticas, productos, procesos o datos internos, usa SOLO las FUENTES. \
Cita cifras, plazos, requisitos y nombres exactamente como aparecen.
4. Si las FUENTES no contienen la respuesta, dilo claramente ("No encontré esa información en los documentos \
disponibles para tu departamento.") y NO uses conocimiento externo ni inventes. Si hay información parcial o \
relacionada, preséntala indicando que es parcial.
5. Si las FUENTES se contradicen, indícalo y menciona de qué documento viene cada dato.
6. Si la pregunta no tiene relación con la cooperativa (cultura general), puedes responder brevemente sin citar fuentes.
7. En "used_sources" lista los números de las [Fuente N] que realmente usaste (vacío si no usaste ninguna).
8. En "tags" incluye de 2 a 5 palabras clave del tema."""

ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "used_sources": {"type": "array", "items": {"type": "integer"}},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["answer", "used_sources", "tags"],
    "additionalProperties": False,
}

FALLBACK_ERROR = "Lo siento, tuve un problema al generar la respuesta. Por favor intenta de nuevo."


# --- Define State ---
class AgentState(TypedDict):
    position: str
    user_department: str
    messages: Annotated[List[BaseMessage], operator.add]
    search_query: str
    context: List[Document]
    answer: dict


# --- Retrieval ---
def search_documents(query_text, department_filter, k=RETRIEVAL_CANDIDATES):
    """Vector search restricted to the categories the user may see."""
    query_vector = global_embedding_service_instance.get_embedding(query_text)
    if query_vector is None:
        log.error("Failed to generate the query embedding")
        return []

    rpc_params = {
        "query_embedding": query_vector,
        "match_threshold": RETRIEVAL_MATCH_THRESHOLD,
        "match_count": k,
        "allowed_categories": department_filter,
    }
    try:
        response = (
            global_supabase_client.schema(SUPABASE_SCHEMA)
            .rpc("match_documents", rpc_params)
            .execute()
        )
    except Exception:
        log.exception("Supabase search failed")
        return []

    documents = []
    for record in response.data:
        metadata = dict(record.get("metadata") or {})
        if "similarity" in record:
            metadata["similarity"] = record["similarity"]
        documents.append(Document(page_content=record.get("content", ""), metadata=metadata))
    return documents


def format_sources(docs):
    parts = []
    for i, doc in enumerate(docs, 1):
        m = doc.metadata
        header = [
            f"Documento: {m.get('filename', 'Desconocido')}",
            f"Departamento: {CATEGORY_LABELS.get(m.get('category'), m.get('category', 'N/D'))}",
        ]
        if m.get("section"):
            header.append(f"Sección: {m['section']}")
        if m.get("page_no"):
            header.append(f"Página: {m['page_no']}")
        parts.append(f"[Fuente {i}] " + " | ".join(header) + f"\n{doc.page_content}")
    return "\n\n".join(parts)


def build_references(docs, used_sources):
    """References come from the chunks' metadata, never from the LLM's memory."""
    picked = [
        docs[i - 1]
        for i in dict.fromkeys(used_sources)
        if isinstance(i, int) and 1 <= i <= len(docs)
    ]
    sources, seen = [], set()
    for doc in picked:
        m = doc.metadata
        source = {
            "document": m.get("filename"),
            "department": CATEGORY_LABELS.get(m.get("category"), m.get("category")),
            "section": m.get("section"),
            "page": m.get("page_no"),
        }
        key = tuple(source.values())
        if key not in seen:
            seen.add(key)
            sources.append(source)

    def unique(values):
        return list(dict.fromkeys(v for v in values if v))

    sections = [
        f"{s['section']} (p. {s['page']})" if s["section"] and s["page"]
        else s["section"] or (f"p. {s['page']}" if s["page"] else None)
        for s in sources
    ]
    return {
        "document_reference": ", ".join(unique(s["document"] for s in sources)),
        "department_reference": ", ".join(unique(s["department"] for s in sources)),
        "section_reference": "; ".join(unique(sections)),
        "sources": sources,
    }


# --- Nodes ---
async def rewrite_query(state: AgentState):
    return {"search_query": await rag.rewrite_question(state["messages"])}


def retrieve_documents(state: AgentState):
    """Retrieves chunks the user's department/position may see, then reranks them."""
    query = state.get("search_query") or state["messages"][-1].content
    filters = get_department_categories(state["user_department"], state.get("position", ""))

    candidates = search_documents(query, filters)
    docs = rag.rerank(query, candidates, RETRIEVAL_K)
    log.info("Retrieved %d candidates, kept %d", len(candidates), len(docs))
    return {"context": docs}


async def generate_answer(state: AgentState):
    docs = state.get("context", [])
    sources = format_sources(docs) or "(No se encontraron fragmentos relevantes.)"
    messages = [{"role": "system", "content": f"{SYSTEM_PROMPT}\n\nFUENTES:\n{sources}"}]
    messages += rag.to_openai_messages(rag.trim_history(state["messages"]))

    try:
        data = await llm.chat_json(messages, "document_answer", ANSWER_SCHEMA)
    except Exception:
        log.exception("Answer generation failed")
        answer = {"answer": FALLBACK_ERROR, "tags": [], **build_references([], [])}
        return {"answer": answer, "messages": [rag.answer_message(FALLBACK_ERROR)]}

    answer = {
        "answer": data["answer"],
        "tags": data["tags"],
        **build_references(docs, data["used_sources"]),
    }
    return {"answer": answer, "messages": [rag.answer_message(data["answer"])]}


# --- Graph Construction ---
workflow = StateGraph(AgentState)

workflow.add_node("rewrite", rewrite_query)
workflow.add_node("retrieve", retrieve_documents)
workflow.add_node("generate", generate_answer)

workflow.set_entry_point("rewrite")
workflow.add_edge("rewrite", "retrieve")
workflow.add_edge("retrieve", "generate")
workflow.add_edge("generate", END)

memory = MemorySaver()

app_graph = workflow.compile(checkpointer=memory)


# --- Main Execution ---
if __name__ == "__main__":
    try:
        asyncio.run(rag.run_cli(app_graph))
    except KeyboardInterrupt:
        print("\nAgent: Goodbye! (Interrupted)")
        sys.exit(0)
