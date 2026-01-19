import os
import json
import requests
import msal
import dateutil.parser
from pathlib import Path
from dotenv import load_dotenv

# --- Indexing imports ---
from supabase import create_client, Client
from docling.document_converter import DocumentConverter
from docling.chunking import HybridChunker  # specific docling chunker
import openai
import google.generativeai as genai

load_dotenv()

# --- Configuration ---
LLM_SERVICE = os.getenv('LLM_SERVICE', 'openai').lower()
LLM_API_KEY = os.getenv('LLM_SERVICE_API_KEY')
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY') # Service role key preferred for writing
SUPABASE_SCHEMA = os.getenv('SUPABASE_SCHEMA', 'public') # Default to public if not set
SUPABASE_TABLE = os.getenv('SUPABASE_TABLE', 'documents') # Default to documents if not set

# --- Helper: Embedding Generator Factory ---
class EmbeddingService:
    def __init__(self):
        self.service = LLM_SERVICE
        self.api_key = LLM_API_KEY
        
        if self.service == 'openai':
            self.client = openai.OpenAI(api_key=self.api_key)
        elif self.service == 'gemini':
            genai.configure(api_key=self.api_key)
        
    def get_embedding(self, text):
        """Generates embedding vector based on selected service."""
        text = text.replace("\n", " ") # Normalize
        
        try:
            if self.service == 'openai':
                response = self.client.embeddings.create(
                    input=[text], model="text-embedding-3-small"
                )
                return response.data[0].embedding
            
            elif self.service == 'gemini':
                # 'models/embedding-001' is standard for Gemini
                result = genai.embed_content(
                    model="models/embedding-001",
                    content=text,
                    task_type="retrieval_document"
                )
                return result['embedding']
            
            elif self.service == 'huggingface':
                # Using HF Inference API
                api_url = "https://api-inference.huggingface.co/pipeline/feature-extraction/sentence-transformers/all-MiniLM-L6-v2"
                headers = {"Authorization": f"Bearer {self.api_key}"}
                response = requests.post(api_url, headers=headers, json={"inputs": text, "options": {"wait_for_model": True}})
                return response.json()
            
            else:
                raise ValueError("Unsupported LLM_SERVICE")
                
        except Exception as e:
            print(f"Error generating embedding: {e}")
            return None

# --- Part 1: SharePoint Sync (Updated) ---
class SharePointSync:
    def __init__(self, download_dir):
        self.tenant_id = os.getenv('OFFICE_365_TENANT_ID')
        self.client_id = os.getenv('OFFICE_365_CLIENT_ID')
        self.client_secret = os.getenv('OFFICE_365_CLIENT_SECRET')
        self.host_name = os.getenv('OFFICE_365_SITE_HOSTNAME')
        self.site_path = os.getenv('OFFICE_365_SITE_NAME')
        self.doc_lib_name = os.getenv('OFFICE_365_DOCUMENT_LIBRARY_NAME')
        
        self.download_dir = download_dir
        self.state_file = Path("sync_state.json")
        self.sync_state = {}
        
        self.scopes = ["https://graph.microsoft.com/.default"]
        self.headers = None
        
        # Track updated files to trigger indexing later
        self.updated_files = [] 

        if self.state_file.exists():
            with open(self.state_file, 'r') as f:
                self.sync_state = json.load(f)

    def authenticate(self):
        app = msal.ConfidentialClientApplication(
            self.client_id, authority=f"https://login.microsoftonline.com/{self.tenant_id}",
            client_credential=self.client_secret
        )
        result = app.acquire_token_for_client(scopes=self.scopes)
        if "access_token" in result:
            self.headers = {'Authorization': f'Bearer {result["access_token"]}'}
        else:
            raise Exception(f"Auth failed: {result.get('error_description')}")

    def get_site_and_drive(self):
        # 1. Get Site
        site_url = f"https://graph.microsoft.com/v1.0/sites/{self.host_name}:{self.site_path}"
        resp = requests.get(site_url, headers=self.headers)
        resp.raise_for_status()
        site_id = resp.json()["id"]

        # 2. Get Drive
        resp = requests.get(f"https://graph.microsoft.com/v1.0/sites/{site_id}/drives", headers=self.headers)
        drive_id = next((d["id"] for d in resp.json()["value"] if d["name"] == self.doc_lib_name), None)
        
        if not drive_id: raise Exception("Drive not found")
        return site_id, drive_id

    def process_folder(self, site_id, drive_id, folder_id="root", current_path=None):
        if current_path is None: current_path = self.download_dir
        
        url = f"https://graph.microsoft.com/v1.0/sites/{site_id}/drives/{drive_id}/items/{folder_id}/children"
        
        while url:
            resp = requests.get(url, headers=self.headers)
            data = resp.json()
            
            for item in data.get('value', []):
                name = item['name']
                local_path = current_path / name
                
                if 'folder' in item:
                    self.process_folder(site_id, drive_id, item['id'], local_path)
                elif 'file' in item:
                    remote_mod = item['lastModifiedDateTime']
                    item_id = item['id']
                    
                    # Check if download needed
                    needs_sync = True
                    if item_id in self.sync_state:
                        saved_time = dateutil.parser.isoparse(self.sync_state[item_id])
                        remote_time = dateutil.parser.isoparse(remote_mod)
                        if remote_time <= saved_time and local_path.exists():
                            needs_sync = False
                    
                    if needs_sync:
                        self.download_file(item['@microsoft.graph.downloadUrl'], local_path)
                        self.sync_state[item_id] = remote_mod
                        self.updated_files.append(local_path) # Mark for indexing
            
            url = data.get('@odata.nextLink')

    def download_file(self, url, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        print(f"Downloading: {path}")
        with requests.get(url, stream=True) as r:
            r.raise_for_status()
            with open(path, 'wb') as f:
                for chunk in r.iter_content(8192): f.write(chunk)

    def run(self):
        self.authenticate()
        site_id, drive_id = self.get_site_and_drive()
        print("Starting SharePoint Sync...")
        self.process_folder(site_id, drive_id)
        
        with open(self.state_file, 'w') as f:
            json.dump(self.sync_state, f, indent=4)
        
        return self.updated_files

# --- Part 2: Knowledge Base Indexer ---
class KnowledgeBaseIndexer:
    def __init__(self, root_dir):
        self.root_dir = root_dir
        self.supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
        self.embedder = EmbeddingService()
        self.converter = DocumentConverter()
        
        # Load schema/table config
        self.db_schema = SUPABASE_SCHEMA
        self.db_table = SUPABASE_TABLE

    def get_category_from_path(self, file_path):
        """Extracts the subfolder structure relative to root_dir."""
        try:
            relative_path = file_path.relative_to(self.root_dir)
            parent = relative_path.parent
            return str(parent) if str(parent) != "." else "Uncategorized"
        except ValueError:
            return "External"

    def index_file(self, file_path):
        print(f"Processing with Docling: {file_path.name}")
        
        try:
            # 1. Convert
            doc_result = self.converter.convert(file_path)
            doc = doc_result.document
            
            category = self.get_category_from_path(file_path)
            
            # 2. Cleanup Old Entries
            # IMPORTANT: We explicitly call .schema() before .table()
            (self.supabase
                 .schema(self.db_schema)
                 .table(self.db_table)
                 .delete()
                 .eq("metadata->>filepath", str(file_path))
                 .execute())

            chunks_to_insert = []
            
            for item in doc.texts:
                text_content = item.text.strip()
                if not text_content or len(text_content) < 50: continue 
                
                # 3. Embed
                vector = self.embedder.get_embedding(text_content)
                if not vector: continue

                # 4. Prepare Payload
                payload = {
                    "content": text_content,
                    "metadata": {
                        "filepath": str(file_path),
                        "filename": file_path.name,
                        "category": category, 
                        "page_no": getattr(item, 'page_no', 1) 
                    },
                    "embedding": vector
                }
                chunks_to_insert.append(payload)

            # 5. Batch Insert
            if chunks_to_insert:
                batch_size = 10
                for i in range(0, len(chunks_to_insert), batch_size):
                    batch = chunks_to_insert[i:i + batch_size]
                    
                    # IMPORTANT: Apply Schema and Table here as well
                    (self.supabase
                        .schema(self.db_schema)
                        .table(self.db_table)
                        .insert(batch)
                        .execute())
                        
                print(f"Indexed {len(chunks_to_insert)} chunks for {file_path.name} in schema '{self.db_schema}'")
            else:
                print(f"No readable text found in {file_path.name}")

        except Exception as e:
            print(f"Failed to index {file_path.name}: {e}")

    def run_indexer(self, files_to_process=None):
        if files_to_process:
            print(f"Indexing {len(files_to_process)} new/modified files to {self.db_schema}.{self.db_table}...")
            for f in files_to_process:
                self.index_file(f)
        else:
            print(f"Full scan indexing to {self.db_schema}.{self.db_table}...")
            for f in self.root_dir.rglob("*"):
                if f.is_file() and f.suffix.lower() in ['.pdf', '.docx', '.pptx', '.md', '.txt']:
                    self.index_file(f)

# --- Part 3: Interactive Chat Agent (New) ---
class ChatAgent:
    def __init__(self):
        self.supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
        self.embedder = EmbeddingService()
        self.db_schema = SUPABASE_SCHEMA
        
        # Configure the LLM for chat generation
        if LLM_SERVICE == 'openai':
            self.chat_client = openai.OpenAI(api_key=LLM_API_KEY)
        elif LLM_SERVICE == 'gemini':
            genai.configure(api_key=LLM_API_KEY)
            self.chat_model = genai.GenerativeModel('gemini-pro')

    def search_documents(self, query_text, match_count=5):
        """Searches the vector database for relevant content."""
        query_vector = self.embedder.get_embedding(query_text)
        
        # Call the RPC function we created in SQL
        # Note: RPC calls ignore .schema(), so we baked the schema 'private' into the SQL function itself.
        # If your function name is just 'match_documents' inside schema 'private', you call it as is.
        try:
            rpc_params = {
                "query_embedding": query_vector,
                "match_threshold": 0.5, # Adjust based on strictness needed
                "match_count": match_count
            }
            
            # Note: The supabase-py client handles schema slightly differently for RPC.
            # Usually, RPC functions are globally accessible if permissions allow, 
            # but if it's strictly inside a schema, ensure your user has search_path set or function is public.
            # Assuming the SQL function 'match_documents' was created in the schema defined:
            
            response = self.supabase.rpc(f"match_documents", rpc_params).execute()
            return response.data
        except Exception as e:
            print(f"Search Error: {e}")
            return []

    def generate_response(self, query, context_chunks):
        """Constructs a prompt and gets an answer from the LLM."""
        
        # 1. Prepare Context
        context_text = "\n\n".join([f"SOURCE ({c['metadata']['filename']}): {c['content']}" for c in context_chunks])
        
        system_prompt = """You are a helpful assistant for a company. 
        Answer the user's question using ONLY the context provided below. 
        If the answer is not in the context, say "I don't have that information in my documents."
        Include the source filename in your answer if relevant."""

        full_prompt = f"Context:\n{context_text}\n\nQuestion: {query}"

        # 2. Call LLM
        if LLM_SERVICE == 'openai':
            response = self.chat_client.chat.completions.create(
                model="gpt-4o", # or gpt-3.5-turbo
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": full_prompt}
                ]
            )
            return response.choices[0].message.content
            
        elif LLM_SERVICE == 'gemini':
            # Gemini format
            prompt = f"{system_prompt}\n\n{full_prompt}"
            response = self.chat_model.generate_content(prompt)
            return response.text
            
        return "LLM Service not configured for Chat."

    def start_chat(self):
        print("\n" + "="*50)
        print("SharePoint Agent Ready. Type 'exit' or 'quit' to stop.")
        print("="*50)
        
        while True:
            user_input = input("\nYou: ")
            if user_input.lower() in ['exit', 'quit', 'bye']:
                print("Agent: Goodbye!")
                break
            
            print("Agent: Thinking...")
            
            # 1. Retrieve
            results = self.search_documents(user_input)
            
            if not results:
                print("Agent: I couldn't find any relevant documents in the database.")
                continue
                
            # 2. Generate
            answer = self.generate_response(user_input, results)
            print(f"Agent: {answer}")


# --- Scheduled Indexing Execution Flow ---
def scheduled_indexing():
    DOWNLOAD_DIR = Path("./downloads")
    
    # 1. Run SharePoint Sync
    syncer = SharePointSync(DOWNLOAD_DIR)
    updated_files = syncer.run()
    
    # 2. Run Indexer
    indexer = KnowledgeBaseIndexer(DOWNLOAD_DIR)
    
    if updated_files:
        indexer.run_indexer(updated_files)
    else:
        print("No new files from SharePoint.")
        

# --- Main Execution Flow ---
if __name__ == "__main__":
    DOWNLOAD_DIR = Path("./downloads")
    
    # 1. Run SharePoint Sync
    syncer = SharePointSync(DOWNLOAD_DIR)
    updated_files = syncer.run()
    
    # 2. Run Indexer
    # Note: If updated_files is empty, it means no changes in SharePoint.
    # However, if the local DB is empty, you might want to force a full scan.
    
    indexer = KnowledgeBaseIndexer(DOWNLOAD_DIR)
    
    if updated_files:
        indexer.run_indexer(updated_files)
    else:
        print("No new files from SharePoint.")
        user_input = input("Do you want to run a full re-indexing of existing local files? (y/n): ")
        if user_input.lower() == 'y':
            indexer.run_indexer()
