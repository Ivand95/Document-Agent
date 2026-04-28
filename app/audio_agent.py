import os
import sys
import asyncio
import re
from typing import Annotated, List, Dict
from typing_extensions import TypedDict
from dotenv import load_dotenv

from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_community.vectorstores import SupabaseVectorStore
from langchain_core.documents import Document
from supabase.client import create_client, Client

from langgraph.graph import StateGraph, END
from audio_ingestion import ChatAgent 
from config import (
    global_supabase_client,
    global_embedding_service_instance,
    SUPABASE_SCHEMA,
    SUPABASE_AUDIO_TABLE # Ensure this points to your audio table
)

load_dotenv()

# Setup Supabase Client
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
SUPABASE_AUDIO_TABLE = os.getenv("SUPABASE_AUDIO_TABLE")
SUPABASE_SCHEMA = os.getenv("SUPABASE_SCHEMA")

# --- Define State ---
class AgentState(TypedDict):
    question: str
    user_department: str
    context: List[Document]
    answer: str

# --- Initialize ChatAgent ---
global_chat_agent_for_graph = ChatAgent()

# --- Nodes ---

def custom_supabase_search(query_text: str, extension_filter: str = None, k: int = 8):
    query_vector = global_embedding_service_instance.get_embedding(query_text)
    if query_vector is None: return []

    rpc_params = {
        "query_embedding": query_vector,
        "match_threshold": 0.2, # Lower threshold for audio
        "match_count": k,
        "filter_extension": extension_filter 
    }

    try:
        response = (
            global_supabase_client.schema(SUPABASE_SCHEMA)
            .rpc("match_conversations", rpc_params)
            .execute()
        )
        
        conversations = []
        for record in response.data:
            conversations.append(Document(
                page_content=record.get("content", ""),
                metadata=record.get("metadata", {})
            ))
        return conversations
    except Exception as e:
        print(f"Search Error: {e}")
        return []

def retrieve_conversations(state: AgentState):
    question = state["question"]

    # Looks for 4-digit numbers in the user's prompt
    ext_match = re.search(r'\b\d{4}\b', question)
    detected_ext = ext_match.group(0) if ext_match else None
    
    if detected_ext:
        print(f"DEBUG: Detected extension {detected_ext} in query. Applying filter.")
    
    # Pass the detected extension to the search
    conversations = custom_supabase_search(question, extension_filter=detected_ext, k=5)

    return {"context": conversations}

async def generate_answer(state: AgentState):
    question = state["question"]
    context_conversations = state["context"]

    # This calls the method in audio_ingestion.py which has the Spanish prompts
    answer_content = await global_chat_agent_for_graph.generate_response(
        question, context_conversations
    )
    return {"answer": answer_content}

# --- Graph Construction ---
workflow = StateGraph(AgentState)
workflow.add_node("retrieve", retrieve_conversations)
workflow.add_node("generate", generate_answer)
workflow.set_entry_point("retrieve")
workflow.add_edge("retrieve", "generate")
workflow.add_edge("generate", END)
app_audio_graph = workflow.compile()

# --- Main Execution ---
if __name__ == "__main__":
    # This runs the interactive loop from audio_ingestion.ChatAgent
    # or you can invoke app_audio_graph.ainvoke(...) for a single turn
    try:
        agent = ChatAgent()
        asyncio.run(agent.start_chat())
    except KeyboardInterrupt:
        sys.exit(0)
