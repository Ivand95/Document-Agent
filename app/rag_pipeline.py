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
        print(f"⬇️ Downloading: {path}")
        with requests.get(url, stream=True) as r:
            r.raise_for_status()
            with open(path, 'wb') as f:
                for chunk in r.iter_content(8192): f.write(chunk)

    def run(self):
        self.authenticate()
        site_id, drive_id = self.get_site_and_drive()
        print("🔄 Starting SharePoint Sync...")
        self.process_folder(site_id, drive_id)
        
        with open(self.state_file, 'w') as f:
            json.dump(self.sync_state, f, indent=4)
        
        return self.updated_files

# --- Part 2: Knowledge Base Indexer (New) ---
class KnowledgeBaseIndexer:
    def __init__(self, root_dir):
        self.root_dir = root_dir
        self.supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
        self.embedder = EmbeddingService()
        self.converter = DocumentConverter() # Docling converter
        
        # We can optionally reuse chunking strategies from Docling
        # But for simplicity, we rely on Docling's default output structure
        
    def get_category_from_path(self, file_path):
        """
        Extracts the subfolder structure relative to root_dir.
        Example: downloads/HR/Policies/file.pdf -> "HR/Policies"
        """
        try:
            relative_path = file_path.relative_to(self.root_dir)
            # Return the parent folder path as string. If it's in root, returns "."
            parent = relative_path.parent
            return str(parent) if str(parent) != "." else "Uncategorized"
        except ValueError:
            return "External"

    def index_file(self, file_path):
        print(f"Processing with Docling: {file_path.name}")
        
        try:
            # 1. Convert/Parse the document
            doc_result = self.converter.convert(file_path)
            doc = doc_result.document
            
            # 2. Extract content & Chunk
            # Docling provides structured output. We can iterate over texts.
            # A simple strategy: Group by paragraphs or headers.
            
            category = self.get_category_from_path(file_path)
            
            # Clean existing entries for this file to avoid duplicates
            self.supabase.table("documents").delete().eq("metadata->>filepath", str(file_path)).execute()

            chunks_to_insert = []
            
            # Iterating through Docling structure
            # Depending on docling version, we might use doc.body.text or iterate items
            # Here we try a simple text extraction per logical block
            
            for item in doc.texts():
                text_content = item.text.strip()
                if not text_content or len(text_content) < 50: continue # Skip empty/short
                
                # 3. Generate Embedding
                vector = self.embedder.get_embedding(text_content)
                if not vector: continue

                # 4. Prepare Payload
                payload = {
                    "content": text_content,
                    "metadata": {
                        "filepath": str(file_path),
                        "filename": file_path.name,
                        "category": category, # <--- THE SUBFOLDER NAME
                        "page_no": getattr(item, 'page_no', 1) # simple fallback
                    },
                    "embedding": vector
                }
                chunks_to_insert.append(payload)

            # 5. Batch Insert to Supabase
            if chunks_to_insert:
                # Supabase insert limit is usually high, but batching by 10 is safe
                batch_size = 10
                for i in range(0, len(chunks_to_insert), batch_size):
                    batch = chunks_to_insert[i:i + batch_size]
                    self.supabase.table("documents").insert(batch).execute()
                print(f"Indexed {len(chunks_to_insert)} chunks for {file_path.name} in category '{category}'")
            else:
                print(f"No readable text found in {file_path.name}")

        except Exception as e:
            print(f"Failed to index {file_path.name}: {e}")

    def run_indexer(self, files_to_process=None):
        """
        If files_to_process is provided (from Sync), only index those.
        Otherwise, scan the whole folder (useful for first run).
        """
        if files_to_process:
            # Only process specific list
            print(f"Indexing {len(files_to_process)} new/modified files...")
            for f in files_to_process:
                self.index_file(f)
        else:
            # Walk entire directory
            print("Full scan indexing...")
            for f in self.root_dir.rglob("*"):
                if f.is_file() and f.suffix.lower() in ['.pdf', '.docx', '.pptx', '.md', '.txt']:
                    self.index_file(f)

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
