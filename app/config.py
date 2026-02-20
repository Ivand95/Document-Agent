import os
from supabase import create_client, Client
import openai
import google.generativeai as genai
from dotenv import load_dotenv


load_dotenv()

# --- Configuration ---
LLM_SERVICE = os.getenv('LLM_SERVICE', 'openai').lower()
LLM_API_KEY = os.getenv('LLM_SERVICE_API_KEY')
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')
SUPABASE_SCHEMA = os.getenv('SUPABASE_SCHEMA', 'public')
SUPABASE_TABLE = os.getenv('SUPABASE_TABLE', 'documents')


# --- Helper: Embedding Generator Factory (from your initial script) ---
class EmbeddingService:
    def __init__(self):
        self.service = LLM_SERVICE
        self.api_key = LLM_API_KEY
        
        if self.service == 'openai':
            self.client = openai.OpenAI(api_key=self.api_key)
        elif self.service == 'gemini':
            genai.configure(api_key=self.api_key)
        
    def get_embedding(self, text):
        text = text.replace("\n", " ")
        try:
            if self.service == 'openai':
                response = self.client.embeddings.create(
                    input=[text], model="text-embedding-3-small"
                )
                return response.data[0].embedding
            elif self.service == 'gemini':
                result = genai.embed_content(
                    model="models/embedding-001",
                    content=text,
                    task_type="retrieval_query" 
                )
                return result['embedding']
            else:
                raise ValueError("Unsupported LLM_SERVICE")
        except Exception as e:
            print(f"Error generating embedding: {e}")
            return None

# --- Initialize Global Supabase Client and Embedder Instance ---
# These will be used by your `custom_supabase_search` function
global_supabase_client: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
global_embedding_service_instance = EmbeddingService()
