import os
import json
import re
import requests
import msal
import asyncio
import dateutil.parser
from pathlib import Path
from dotenv import load_dotenv

# --- Indexing imports ---
from supabase import create_client, Client
from docling.document_converter import DocumentConverter
from docling.datamodel.base_models import InputFormat
import openai
import google.generativeai as genai

from langchain_core.documents import Document
from config import (
    global_supabase_client,
    global_embedding_service_instance,
    SUPABASE_SCHEMA,
    SUPABASE_KEY,
    SUPABASE_URL,
    LLM_SERVICE,
    LLM_API_KEY,
    SUPABASE_AUDIO_TABLE,
)

load_dotenv()

# --- Part 1: SharePoint Sync ---
class SharePointSync:
    def __init__(self, download_dir):
           
        self.tenant_id = os.getenv("OFFICE_365_TENANT_ID")
        self.client_id = os.getenv("OFFICE_365_CONVERSATION_CLIENT_ID")
        self.client_secret = os.getenv("OFFICE_365_CONVERSATION_CLIENT_SECRET")
        self.host_name = os.getenv("OFFICE_365_CONVERSATION_SITE_HOSTNAME")
        self.site_path = os.getenv("OFFICE_365__SITE_NAME")
        self.doc_lib_name = os.getenv("OFFICE_365_CONVERSATION_DOCUMENT_LIBRARY_NAME")

        self.download_dir = download_dir
        self.state_file = Path("audio_sync_state.json")
        self.sync_state = {}
        self.updated_files = []
        self.headers = None
        self.scopes = ["https://graph.microsoft.com/.default"]

        if self.state_file.exists():
            with open(self.state_file, "r") as f:
                self.sync_state = json.load(f)
        
        # REGEX: [Name]_Ext-Phone_Timestamp.wav (Phone must be 7-15 digits to filter out extension-to-extension)
        # REGEX EXPLANATION:
        # ^(\[.*?\])? -> Optional [Name]
        # _           -> Underscore separator
        # (\d{3,4})   -> 3 or 4 digit extension
        # -           -> Hyphen separator
        # (\d{7,15})  -> 7 to 15 digit phone number (Filters out short 4-digit extensions)
        # _           -> Underscore separator
        # (\d+)       -> Timestamp digits
        # .*?\.wav$   -> Ends with .wav
        self.audio_pattern = re.compile(r"^(\[.*?\])?_(\d{3,4})-(\d{7,15})_(\d+).*?\.wav$", re.IGNORECASE)

    def authenticate(self):
        app = msal.ConfidentialClientApplication(
            self.client_id,
            authority=f"https://login.microsoftonline.com/{self.tenant_id}",
            client_credential=self.client_secret,
        )
        result = app.acquire_token_for_client(scopes=self.scopes)
        if "access_token" in result:
            self.headers = {"Authorization": f'Bearer {result["access_token"]}'}
        else:
            raise Exception(f"Auth failed: {result.get('error_description')}")

    def get_site_and_drive(self):
        site_url = f"https://graph.microsoft.com/v1.0/sites/{self.host_name}:{self.site_path}"
        resp = requests.get(site_url, headers=self.headers)
        resp.raise_for_status()
        site_id = resp.json()["id"]

        resp = requests.get(f"https://graph.microsoft.com/v1.0/sites/{site_id}/drives", headers=self.headers)
        drive_id = next((d["id"] for d in resp.json()["value"] if d["name"] == self.doc_lib_name), None)
        if not drive_id: raise Exception("Drive not found")
        return site_id, drive_id

    def is_valid_audio_file(self, filename):
        return bool(self.audio_pattern.match(filename))

    def process_folder(self, site_id, drive_id, folder_id="root", current_path=None):
        if current_path is None:
            current_path = self.download_dir

        url = f"https://graph.microsoft.com/v1.0/sites/{site_id}/drives/{drive_id}/items/{folder_id}/children"

        while url:
            resp = requests.get(url, headers=self.headers)
            data = resp.json()

            for item in data.get("value", []):
                name = item["name"]
                local_path = current_path / name

                if "folder" in item:
                    self.process_folder(site_id, drive_id, item["id"], local_path)
                elif "file" in item:
                    if not self.is_valid_audio_file(name):
                        continue
                    
                    remote_mod = item["lastModifiedDateTime"]
                    item_id = item["id"]

                    needs_sync = True
                    if item_id in self.sync_state:
                        saved_time = dateutil.parser.isoparse(self.sync_state[item_id])
                        remote_time = dateutil.parser.isoparse(remote_mod)
                        if remote_time <= saved_time and local_path.exists():
                            needs_sync = False

                    if needs_sync:
                        self.download_file(item["@microsoft.graph.downloadUrl"], local_path)
                        self.sync_state[item_id] = remote_mod
                        self.updated_files.append(local_path)

            url = data.get("@odata.nextLink")

    def download_file(self, url, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        print(f"Downloading: {path}")
        with requests.get(url, stream=True) as r:
            r.raise_for_status()
            with open(path, "wb") as f:
                for chunk in r.iter_content(8192):
                    f.write(chunk)

    def run(self):
        self.authenticate()
        site_id, drive_id = self.get_site_and_drive()
        print("Starting SharePoint Sync for Audio Files...")
        self.process_folder(site_id, drive_id)

        with open(self.state_file, "w") as f:
            json.dump(self.sync_state, f, indent=4)

        return self.updated_files

# --- Part 2: Audio Indexer ---
class KnowledgeBaseIndexer:
    def __init__(self, root_dir):
        self.root_dir = root_dir
        self.supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
        self.embedder = global_embedding_service_instance
        self.converter = DocumentConverter() 
        self.db_schema = SUPABASE_SCHEMA
        self.db_table = SUPABASE_AUDIO_TABLE
        self.audio_pattern = re.compile(r"^(\[(.*?)\])?_(\d{3,4})-(\d{7,15})_(\d+).*?\.wav$", re.IGNORECASE)

    def get_category_from_path(self, file_path):
        try:
            relative_path = file_path.relative_to(self.root_dir)
            return "Uncategorized" if str(relative_path.parent) == "." else relative_path.parts[0]
        except ValueError:
            return "External"

    def extract_metadata_from_name(self, filename):
        match = self.audio_pattern.match(filename)
        if match:
            return {
                "employee_name": match.group(2) if match.group(2) else "Unknown",
                "extension": match.group(3),
                "phone_number": match.group(4),
                "timestamp_raw": match.group(5),
            }
        return {}

    def index_file(self, file_path):
        print(f"Transcribing and Indexing Audio: {file_path.name}")
        try:
            result = self.converter.convert(file_path)
            file_meta = self.extract_metadata_from_name(file_path.name)
            category = self.get_category_from_path(file_path)

            (self.supabase.schema(self.db_schema)
                .table(self.db_table)
                .delete()
                .eq("metadata->>filepath", str(file_path))
                .execute())

            chunks_to_insert = []
            for item in result.document.texts:
                text_content = item.text.strip()
                if not text_content or len(text_content) < 20: continue

                vector = self.embedder.get_embedding(text_content)
                if not vector: continue

                payload = {
                    "content": text_content,
                    "metadata": {
                        "filepath": str(file_path),
                        "filename": file_path.name,
                        "category": category,
                        "content_type": "audio_transcript",
                        **file_meta 
                    },
                    "embedding": vector,
                }
                chunks_to_insert.append(payload)

            if chunks_to_insert:
                for i in range(0, len(chunks_to_insert), 10):
                    self.supabase.schema(self.db_schema).table(self.db_table).insert(chunks_to_insert[i:i+10]).execute()
                print(f"Indexed {len(chunks_to_insert)} chunks for {file_path.name}")
            
        except Exception as e:
            print(f"Failed to process audio {file_path.name}: {e}")

    def run_indexer(self, files_to_process=None):
        if files_to_process:
            for f in files_to_process:
                self.index_file(f)
        else:
            for f in self.root_dir.rglob("*.wav"):
                if f.is_file() and self.audio_pattern.match(f.name):
                    self.index_file(f)


# --- Part 3: Interactive Chat Agent Audio ---
class ChatAgent:
    def __init__(self):
        self.supabase: Client = global_supabase_client
        self.embedder = global_embedding_service_instance
        self.db_schema = SUPABASE_SCHEMA

        # Configure the LLM for chat generation
        if LLM_SERVICE == "openai":
            self.chat_client = openai.OpenAI(api_key=LLM_API_KEY)
        elif LLM_SERVICE == "gemini":
            genai.configure(api_key=LLM_API_KEY)
            self.chat_model = genai.GenerativeModel("gemini-pro")

    def search_documents(self, query_text, match_count=5):
        """Searches the vector database for relevant content."""
        query_vector = self.embedder.get_embedding(query_text)

        # Call the RPC function we created in SQL
        # Note: RPC calls ignore .schema(), so we baked the schema 'private' into the SQL function itself.
        # If your function name is just 'match_documents' inside schema 'private', you call it as is.
        try:
            rpc_params = {
                "query_embedding": query_vector,
                "match_threshold": 0.5,  # Adjust based on strictness needed
                "match_count": match_count,
            }

            # Note: The supabase-py client handles schema slightly differently for RPC.
            # Usually, RPC functions are globally accessible if permissions allow,
            # but if it's strictly inside a schema, ensure your user has search_path set or function is public.
            # Assuming the SQL function 'match_documents' was created in the schema defined:

            response = (
                self.supabase.schema(self.db_schema)
                .rpc("match_conversations", rpc_params)
                .execute()
            )

            return response.data

        except Exception as e:
            print(f"Search Error: {e}")
            return []

    async def generate_response(self, query, context_chunks):
        """Constructs a prompt and gets an answer from the LLM."""

        # 1. Prepare Context - This section is the critical fix
        processed_context_chunks = []
        for chunk in context_chunks:
            if isinstance(chunk, Document):
                # Convert Document object back to a dictionary format expected by the join
                processed_context_chunks.append(
                    {"content": chunk.page_content, "metadata": chunk.metadata}
                )
            else:
                # If a dictionary somehow comes through (e.g., from old console path), use it as is
                processed_context_chunks.append(chunk)

        has_context = len(processed_context_chunks) > 0

        if has_context:
            context_text = "\n\n".join(
                [
                    f"SOURCE ({c['metadata'].get('filename', 'Unknown') }): {c['content']}"
                    for c in processed_context_chunks
                ]
            )
        else:
            context_text = "No specific documents found."

        # 2. System Prompt
        system_prompt = """You are a helpful, friendly, and professional AI assistant for a company.
        
        Guidelines:
        1. If the user's input is a greeting, small talk, or a general question (like 'How are you?' or 'What is the capital of France?'), answer naturally and amicably without referencing documents.
        2. If the user asks a specific question about the conversations, calls, transcripts, or any other audio related information, use the provided Context to answer as detailed as possible.
        3. If the question requires audio related information but the information is NOT in the Context, politely say: "I'm sorry, I couldn't find that specific information in the audio conversations available to me." and provide the most relevant information available.
        4. If chunks of text are provided, use them to answer the question.
        5. Always maintain a polite and helpful tone.
        6. Always answer in Spanish unless the user asks for information in another language.
        7. Always return a summary of the conversation, call, or transcript in the answer.
        

        Desired Output:
        Respuesta: [Answer]
        

        Fecha de la conversación: [Conversation date]
        Nombre del empleado: [Employee name]
        Extension: [Extension number]
        Etiquetas: [Tags]
        Sumario: [Summary]
        
        

        Example:
        Respuesta: "The conversation between John Doe and Jane Smith was about the project XYZ..."
       
        Fecha de la conversación: "2026-04-13"
        Nombre del empleado: "John Doe"
        Extension: "1234"
        Etiquetas: "project XYZ, conversation, call, transcript"
        Sumario: "The conversation between John Doe and Jane Smith was about the project XYZ..."
        """

        full_prompt = f"Context:\n{context_text}\n\nQuestion: {query}"

        # 3. Call LLM
        if LLM_SERVICE == "openai":
            # Run the asynchronous OpenAI call in a background thread
            response = await asyncio.to_thread(
                self.chat_client.chat.completions.create,
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": full_prompt},
                ],
            )
            return response.choices[0].message.content

        elif LLM_SERVICE == "gemini":
            # Run the asynchronous Gemini call in a background thread
            prompt = f"{system_prompt}\n\n{full_prompt}"
            response = await asyncio.to_thread(self.chat_model.generate_content, prompt)
            return response.text

        return "LLM Service not configured for Chat."

    async def start_chat(self):
        print("\n" + "=" * 50)
        print("SharePoint Agent Ready. Type 'exit' or 'quit' to stop.")
        print("=" * 50)

        while True:
            user_input = input("\nYou: ")
            if user_input.lower() in ["exit", "quit", "bye"]:
                print("Agent: Goodbye!")
                break

            # 1. Retrieve
            results = self.search_documents(user_input)

            # 2. Generate
            if not results:
                answer = await self.generate_response(user_input, [])
                print(f"Agent: {answer}")
                continue

            answer = await self.generate_response(user_input, results)
            print(f"Agent: {answer}")



# --- Execution ---
if __name__ == "__main__":
    DOWNLOAD_DIR = Path("./downloads_audio")
    syncer = SharePointSync(DOWNLOAD_DIR)
    updated_files = syncer.run()

    indexer = KnowledgeBaseIndexer(DOWNLOAD_DIR)
    if updated_files:
        indexer.run_indexer(updated_files)
    else:
        print("No new audio files. Running full scan...")
        indexer.run_indexer()



