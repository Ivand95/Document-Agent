import os
import json
import asyncio
from pathlib import Path
from dotenv import load_dotenv

# --- Indexing imports ---
from supabase import create_client, Client
from docling.document_converter import DocumentConverter
from docling.chunking import HybridChunker  # specific docling chunker
import openai
import google.generativeai as genai

from langchain_core.documents import Document
from langchain_core.messages import HumanMessage, AIMessage, BaseMessage
from sharepoint_sync import SharePointSyncBase, data_dir, db_key
from config import (
    global_supabase_client,
    global_embedding_service_instance,
    SUPABASE_SCHEMA,
    SUPABASE_KEY,
    SUPABASE_URL,
    LLM_SERVICE,
    LLM_API_KEY,
    SUPABASE_TABLE,
)

load_dotenv()


# --- Part 1: SharePoint Sync ---
# Formats Docling can convert (legacy .doc/.ppt/.xls are not supported by it)
SUPPORTED_EXTENSIONS = {
    ".pdf",
    ".docx",
    ".pptx",
    ".md",
    ".txt",
    ".csv",
    ".xlsx",
    ".png",
    ".jpg",
    ".jpeg",
}


class SharePointSync(SharePointSyncBase):
    def __init__(self, download_dir, force=False, limit=None, folder_path=None):
        super().__init__(
            download_dir=download_dir,
            state_file=data_dir() / "sync_state.json",
            site_path=os.getenv("OFFICE_365_SITE_NAME"),
            doc_lib_name=os.getenv("OFFICE_365_DOCUMENT_LIBRARY_NAME"),
            root_folder=folder_path,
            force=force,
            limit=limit,
        )

    def is_valid_file(self, name):
        return Path(name).suffix.lower() in SUPPORTED_EXTENSIONS


# --- Part 2: Knowledge Base Indexer ---
class KnowledgeBaseIndexer:
    def __init__(self, root_dir):
        self.root_dir = root_dir
        self.supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
        self.embedder = global_embedding_service_instance
        self.converter = DocumentConverter()

        # Load schema/table config
        self.db_schema = SUPABASE_SCHEMA
        self.db_table = SUPABASE_TABLE

    def get_category_from_path(self, file_path):
        """Extracts the subfolder structure relative to root_dir."""
        try:
            relative_path = file_path.relative_to(self.root_dir)

            # Check if the file is directly in the root directory
            if str(relative_path.parent) == ".":
                return "Uncategorized"

            # Extract the first part of the path (the top-level folder)
            return relative_path.parts[0]

        except ValueError:
            return "External"

    def delete_file(self, file_path) -> bool:
        """Removes every chunk stored for a file. Returns False on failure."""
        try:
            (
                self.supabase.schema(self.db_schema)
                .table(self.db_table)
                .delete()
                .eq("metadata->>filepath", db_key(file_path))
                .execute()
            )
            return True
        except Exception as e:
            print(f"Failed to delete chunks for {file_path}: {e}")
            return False

    def index_file(self, file_path) -> bool:
        """Returns True when the file is fully indexed (or has nothing to index)."""
        print(f"Processing with Docling: {file_path.name}")

        try:
            # 1. Convert
            doc_result = self.converter.convert(file_path)
            doc = doc_result.document

            category = self.get_category_from_path(file_path)

            chunks_to_insert = []

            for item in doc.texts:
                text_content = item.text.strip()
                if not text_content or len(text_content) < 50:
                    continue

                # 2. Embed
                vector = self.embedder.get_embedding(text_content)
                if not vector:
                    # Keep the old chunks and retry on the next run instead of storing a partial file
                    print(f"Embedding failed for {file_path.name}; keeping old chunks.")
                    return False

                # 3. Prepare Payload
                payload = {
                    "content": text_content,
                    "metadata": {
                        "filepath": db_key(file_path),
                        "filename": file_path.name,
                        "category": category,
                        "page_no": getattr(item, "page_no", 1),
                    },
                    "embedding": vector,
                }
                chunks_to_insert.append(payload)

            # 4. Replace old entries only once the new chunks are ready
            if not self.delete_file(file_path):
                return False

            # 5. Batch Insert
            if chunks_to_insert:
                batch_size = 10
                for i in range(0, len(chunks_to_insert), batch_size):
                    batch = chunks_to_insert[i : i + batch_size]

                    # IMPORTANT: Apply Schema and Table here as well
                    (
                        self.supabase.schema(self.db_schema)
                        .table(self.db_table)
                        .insert(batch)
                        .execute()
                    )

                print(
                    f"Indexed {len(chunks_to_insert)} chunks for {file_path.name} in schema '{self.db_schema}'"
                )
            else:
                print(f"No readable text found in {file_path.name}")
            return True

        except Exception as e:
            print(f"Failed to index {file_path.name}: {e}")
            return False

    def run_indexer(self, files_to_process=None):
        """Indexes the given files (or every local file). Returns the successfully indexed paths."""
        if files_to_process:
            print(
                f"Indexing {len(files_to_process)} new/modified files to {self.db_schema}.{self.db_table}..."
            )
            files = files_to_process
        else:
            print(f"Full scan indexing to {self.db_schema}.{self.db_table}...")
            files = [
                f
                for f in self.root_dir.rglob("*")
                if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS
            ]
        return [f for f in files if self.index_file(f)]


# --- Part 3: Interactive Chat Agent (New) ---
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
                .rpc("match_documents", rpc_params)
                .execute()
            )

            return response.data

        except Exception as e:
            print(f"Search Error: {e}")
            return []

    async def generate_response(self, query, message_history, context_chunks):
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
        system_prompt = """You are a helpful, friendly, and professional AI assistant for a company. You always answer in JSON format.
        
        Guidelines:
        1. If the user's input is a greeting, small talk, or a general question (like 'How are you?' or 'What is the capital of France?'), answer naturally and amicably without referencing documents.
        2. If the user asks a specific question about the company, projects, or internal data, use the provided Context to answer as detailed as possible.
        3. If the question requires internal data but the information is NOT in the Context, politely say: "I'm sorry, I couldn't find that specific information in the company documents available to me." and provide the most relevant information available.
        4. If chunks of text are provided, use them to answer the question.
        5. Always maintain a polite and helpful tone.
        

        Desired Output:
        {
            "answer": [Answer] or "No disponible",
            "document_reference": [Document name] or "No disponible",
            "department_reference": [Category] or "No disponible",
            "section_reference": [Section name] or "No disponible",
            "tags": [Tags] or "No disponible"
        }

        Example:
        {
            "answer": "The employee benefits are as follows:...",
            "document_reference": "Company Policy Manual",
            "department_reference": "HR",
            "section_reference": "Employee Benefits",
            "tags": "employee benefits, company policy, employee handbook"
        }
        """

        full_prompt = f"Context:\n{context_text}\n\nQuestion: {query}"

        messages = [
            {"role": "system", "content": system_prompt + f"\nContexto de documentos:\n{context_text}"}
        ]

        # Add the conversation history
        for msg in message_history:
            role = "user" if isinstance(msg, HumanMessage) else "assistant"
            messages.append({"role": role, "content": msg.content})

        # 3. Call LLM
        if LLM_SERVICE == "openai":
            # Run the asynchronous OpenAI call in a background thread
            response = await asyncio.to_thread(
                self.chat_client.chat.completions.create,
                model="gpt-4o",
                response_format={ "type": "json_object" },
                messages=messages
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
            try:
                # Pretty print if it's a valid JSON string
                parsed = json.loads(answer.replace("```json", "").replace("```", ""))
                print(f"Agent (JSON):\n{json.dumps(parsed, indent=4, ensure_ascii=False)}")
            except:
                print(f"Agent: {answer}")


# --- Scheduled Indexing Execution Flow ---
def scheduled_indexing(force=False, limit=None, folder_path=None):
    """Syncs SharePoint, indexes new/modified files and drops removed ones. Returns a summary."""
    DOWNLOAD_DIR = data_dir() / "downloads"

    # 1. Run SharePoint Sync
    syncer = SharePointSync(
        DOWNLOAD_DIR, force=force, limit=limit, folder_path=folder_path
    )
    updated_files = syncer.run()

    # 2. Run Indexer
    indexer = KnowledgeBaseIndexer(DOWNLOAD_DIR)

    indexed = indexer.run_indexer(updated_files) if updated_files else []
    if not updated_files:
        print("No new files from SharePoint.")

    # 3. Only files that were indexed are recorded as synced
    for path in indexed:
        syncer.commit(path)

    # 4. Drop chunks of files removed or moved in SharePoint
    deleted = syncer.apply_removals(indexer.delete_file)

    return {
        "synced": len(updated_files),
        "indexed": len(indexed),
        "failed": len(updated_files) - len(indexed),
        "deleted": deleted,
    }


# --- Main Execution Flow ---
if __name__ == "__main__":
    summary = scheduled_indexing()
    print(summary)

    # If SharePoint had no changes but the local DB is empty, you might want to force a full scan.
    if not summary["synced"]:
        user_input = input(
            "Do you want to run a full re-indexing of existing local files? (y/n): "
        )
        if user_input.lower() == "y":
            KnowledgeBaseIndexer(data_dir() / "downloads").run_indexer()
