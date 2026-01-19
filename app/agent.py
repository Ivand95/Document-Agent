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

# --- Nodes ---

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
    docs = vector_store.similarity_search(
        question, 
        k=4, 
        filter=filters
    )
    
    return {"context": docs}

def generate_answer(state: AgentState):
    """
    Generates answer using retrieved context.
    """
    llm = ChatOpenAI(model="gpt-4o", temperature=0)
    
    docs_content = "\n\n".join([d.page_content for d in state["context"]])
    
    system_prompt = f"""You are a helpful assistant for the {state['user_department']} department.
    Use the following context to answer the user's question.
    
    Context:
    {docs_content}
    """
    
    messages = [
        ("system", system_prompt),
        ("user", state["question"])
    ]
    
    response = llm.invoke(messages)
    return {"answer": response.content}

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