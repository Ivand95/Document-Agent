import os
import sys
from typing import Annotated, List, Dict
from typing_extensions import TypedDict
from dotenv import load_dotenv

from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_community.vectorstores import SupabaseVectorStore
from langchain_core.documents import Document
from supabase.client import create_client, Client

from langgraph.graph import StateGraph, END
from indexer import ChatAgent
from config import global_supabase_client, global_embedding_service_instance, SUPABASE_SCHEMA


load_dotenv()

# Setup Supabase Client
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
SUPABASE_SCHEMA = os.getenv('SUPABASE_SCHEMA', 'public') # Default to public if not set
SUPABASE_TABLE = os.getenv('SUPABASE_TABLE', 'documents') # Default to documents if not set

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

embeddings = OpenAIEmbeddings(api_key=os.getenv("LLM_SERVICE_API_KEY"))

# Initialize Vector Store
vector_store = SupabaseVectorStore(
    client=supabase,
    embedding=embeddings,
    table_name=f"{SUPABASE_SCHEMA}.{SUPABASE_TABLE}", # Ensure this matches your table
    query_name="match_documents", # Ensure you have the RPC function in SQL
)

# --- Define State ---
class AgentState(TypedDict):
    question: str
    # This is the security context passed from the API
    user_department: str 
    context: List[Document]
    answer: str

# --- Initialize ChatAgent ---
global_chat_agent_for_graph = ChatAgent()

# --- Nodes ---

def custom_supabase_search(query_text: str, department_filter: str, k: int = 4):
    print(f"DEBUG: custom_supabase_search called with query='{query_text}', department='{department_filter}'")
    
    # 1. Generate Embedding using the global instance
    query_vector = global_embedding_service_instance.get_embedding(query_text)
    
    if query_vector is None:
        print("ERROR: Failed to generate embedding for query. Returning empty documents.")
        return []

    # 2. Prepare RPC Parameters (still assuming the SQL uses `filter jsonb`)
    rpc_params = {
        "query_embedding": query_vector,
        "match_threshold": 0.5,
        "match_count": k,
        "filter": {"category": department_filter}
    }

    try:
        # 3. Execute RPC Call using the global client and explicit schema
        response = (global_supabase_client
                    .schema(SUPABASE_SCHEMA) # Explicitly use the configured schema
                    .rpc("match_documents", rpc_params)
                    .execute())
        
        print(f"DEBUG: Supabase RPC response data length: {len(response.data)}")
        if not response.data:
            print("DEBUG: No documents returned from Supabase RPC.")

        # 4. Convert Supabase response dictionaries to LangChain Document objects
        documents = []
        for record in response.data:
            content = record.get("content", "")
            metadata = record.get("metadata", {})
            
            doc = Document(page_content=content, metadata=metadata)
            documents.append(doc)
        
        print(f"DEBUG: Converted {len(documents)} documents to LangChain Document objects.")
        return documents

    except Exception as e:
        print(f"Supabase Search Error in custom_supabase_search: {e}")
        return []

def retrieve_documents(state: AgentState):
    """
    Retrieves documents filtering by the user's department.
    """
    department = state["user_department"]
    question = state["question"]
    
    print(f"--- Retrieving for Department: {department} ---")
    
    # METADATA FILTERING
    # This assumes your vector metadata looks like: {"department": "HR", "source": "..."}
    filters = {"category": department, "category": "General", "category": "OTROS"}
    
    # Perform similarity search with filter
    docs = custom_supabase_search(question, department, 4)
    
    return {"context": docs}

def generate_answer(state: AgentState):
    """
    Generates answer using retrieved context by leveraging the ChatAgent's logic.
    """
    print("--- GENERATING ANSWER ---")
    
    question = state["question"]
    context_documents = state["context"] # This is already a list of Document objects

    answer_content = global_chat_agent_for_graph.generate_response(question, context_documents)
    
    print(f"Generated answer: {answer_content[:100]}...") # Print first 100 chars
    
    return {"answer": answer_content}
# --- Graph Construction ---

workflow = StateGraph(AgentState)

workflow.add_node("retrieve", retrieve_documents)
workflow.add_node("generate", generate_answer)

workflow.set_entry_point("retrieve")
workflow.add_edge("retrieve", "generate")
workflow.add_edge("generate", END)

app_graph = workflow.compile()


# --- Main Execution ---
if __name__ == "__main__":
    try:
        agent = ChatAgent()
        agent.start_chat()
    except KeyboardInterrupt:
        print("\nAgent: Goodbye! (Interrupted)")
        sys.exit(0)
    except Exception as e:
        print(f"\nCritical Error starting chat: {e}")