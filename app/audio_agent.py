import os
import sys
import asyncio
import re
import json
import operator
from typing import Annotated, List, Dict
from typing_extensions import TypedDict
from dotenv import load_dotenv

from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_community.vectorstores import SupabaseVectorStore
from langchain_core.documents import Document
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
from langgraph.checkpoint.memory import MemorySaver
from supabase.client import create_client, Client

from langgraph.graph import StateGraph, END
from audio_ingestion import ChatAgent
from config import (
    global_supabase_client,
    global_embedding_service_instance,
    SUPABASE_SCHEMA,
    SUPABASE_AUDIO_TABLE,  # Ensure this points to your audio table
)

load_dotenv()

# Setup Supabase Client
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
SUPABASE_AUDIO_TABLE = os.getenv("SUPABASE_AUDIO_TABLE")
SUPABASE_SCHEMA = os.getenv("SUPABASE_SCHEMA")


# --- Define State ---
class AgentState(TypedDict):
    position: str
    user_department: str
    context: List[Document]
    answer: dict
    messages: Annotated[List[BaseMessage], operator.add]


# --- Initialize ChatAgent ---
global_chat_agent_for_graph = ChatAgent()

# --- Nodes ---


def custom_supabase_search(query_text, extension=None, date=None, recipient=None, k=5):
    query_vector = global_embedding_service_instance.get_embedding(query_text)
    
    rpc_params = {
        "query_embedding": query_vector,
        "match_threshold": 0.2,
        "match_count": k,
        "filter_extension": extension,
        "filter_date": date,
        "filter_recipient": recipient
    }

    try:
        response = (
            global_supabase_client.schema(SUPABASE_SCHEMA)
            .rpc("match_conversations", rpc_params)
            .execute()
        )

        conversations = []
        for record in response.data:
            conversations.append(
                Document(
                    page_content=record.get("content", ""),
                    metadata=record.get("metadata", {}),
                )
            )
        return conversations
    except Exception as e:
        print(f"Search Error: {e}")
        return []


def retrieve_conversations(state: AgentState):
    if not state.get("messages"): return {"context": []}
    
    last_message = state["messages"][-1].content
    
    # --- Extraction Logic ---
    # 1. Detect Extension (4 digits)
    ext_match = re.search(r'\b\d{4}\b', last_message)
    
    # 2. Detect Date (YYYY-MM-DD)
    date_match = re.search(r'\d{4}-\d{2}-\d{2}', last_message)
    
    # 3. Detect Recipient (Phone number, usually 7-11 digits)
    # We look for long numbers that aren't the date
    recipient_match = re.search(r'\b\d{7,15}\b', last_message)
    
    conversations = custom_supabase_search(
        query_text=last_message,
        extension=ext_match.group(0) if ext_match else None,
        date=date_match.group(0) if date_match else None,
        recipient=recipient_match.group(0) if recipient_match else None,
        k=60
    )

    return {"context": conversations}


async def generate_answer(state: AgentState):
    # Pass the whole message history to the LLM so it has context
    history = state["messages"]
    context_docs = state["context"]
    query = history[-1].content if history else ""

    # We modify ChatAgent.generate_response to accept the history (see Step 3)
    raw_json_str = await global_chat_agent_for_graph.generate_response(
        query, history, context_docs
    )

    try:
        structured_data = json.loads(raw_json_str)
        # Create an AIMessage to append to the graph state
        ai_message = AIMessage(content=raw_json_str)
        return {"answer": structured_data, "messages": [ai_message]}
    except:
        return {
            "answer": {"error": "JSON Error"},
            "messages": [AIMessage(content=raw_json_str)],
        }


# Example of how to invoke and see the result
async def test_run():
    inputs = {
        "question": "Dame un resumen de la extension 1055",
        "user_department": "General",
    }
    final_state = await app_audio_graph.ainvoke(inputs)

    ans = final_state["answer"]
    print(f"Empleado: {ans.get('nombre_empleado')}")
    print(f"Resumen: {ans.get('sumario')}")


if __name__ == "__main__":
    asyncio.run(test_run())


# --- Graph Construction ---
workflow = StateGraph(AgentState)
workflow.add_node("retrieve", retrieve_conversations)
workflow.add_node("generate", generate_answer)
workflow.set_entry_point("retrieve")
workflow.add_edge("retrieve", "generate")
workflow.add_edge("generate", END)

memory = MemorySaver()

app_audio_graph = workflow.compile(checkpointer=memory)

# --- Main Execution ---
if __name__ == "__main__":
    # This runs the interactive loop from audio_ingestion.ChatAgent
    # or you can invoke app_audio_graph.ainvoke(...) for a single turn
    try:
        agent = ChatAgent()
        asyncio.run(agent.start_chat())
    except KeyboardInterrupt:
        sys.exit(0)
