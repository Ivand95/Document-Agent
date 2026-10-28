import os
import json
import re
import asyncio
from pathlib import Path
from dotenv import load_dotenv

from langchain_core.messages import BaseMessage, HumanMessage, AIMessage

# --- Indexing imports ---
from supabase import create_client, Client
from docling.document_converter import AudioFormatOption, DocumentConverter
from docling.datamodel.base_models import InputFormat
from docling.datamodel import asr_model_specs
from docling.datamodel.pipeline_options import AsrPipelineOptions
from docling.pipeline.asr_pipeline import AsrPipeline
import openai
import google.generativeai as genai

from langchain_core.documents import Document
from sharepoint_sync import SharePointSyncBase, data_dir, db_key
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
# Folder inside the conversations library that holds the 3CX call recordings
DEFAULT_AUDIO_FOLDER = "3CX Recordings"


class SharePointSync(SharePointSyncBase):
    def __init__(self, download_dir, force=False, limit=None, folder_path=None):
        super().__init__(
            download_dir=download_dir,
            state_file=data_dir() / "audio_sync_state.json",
            site_path=os.getenv("OFFICE_365_CONVERSATION_SITE_NAME"),
            doc_lib_name=os.getenv("OFFICE_365_CONVERSATION_DOCUMENT_LIBRARY_NAME"),
            # None = env / default folder; "" = whole library
            root_folder=(
                os.getenv("OFFICE_365_CONVERSATION_FOLDER_PATH", DEFAULT_AUDIO_FOLDER)
                if folder_path is None
                else folder_path
            ),
            force=force,
            limit=limit,
        )

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
        self.audio_pattern = re.compile(
            r"^(\[.*?\])?_(\d{3,4})-(\d{7,15})_(\d+).*?\.wav$", re.IGNORECASE
        )

    def is_valid_audio_file(self, filename):
        return bool(self.audio_pattern.match(filename))

    def is_valid_file(self, name):
        return self.is_valid_audio_file(name)


# --- Part 2: Audio Indexer ---
class KnowledgeBaseIndexer:
    def __init__(self, root_dir):
        # Configure ASR pipeline
        pipeline_options = AsrPipelineOptions(
            asr_options=asr_model_specs.WHISPER_TURBO, language="es"
        )
        format_options = {
            InputFormat.AUDIO: AudioFormatOption(
                pipeline_cls=AsrPipeline,
                pipeline_options=pipeline_options,
            )
        }
        self.root_dir = root_dir
        self.supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
        self.embedder = global_embedding_service_instance
        self.converter = DocumentConverter(format_options=format_options)
        self.db_schema = SUPABASE_SCHEMA
        self.db_table = SUPABASE_AUDIO_TABLE
        self.audio_pattern = re.compile(
            r"^(\[(.*?)\])?_(\d{3,4})-(\d{7,15})_(\d+).*?\.wav$", re.IGNORECASE
        )

        if LLM_SERVICE == "openai":
            self.chat_client = openai.OpenAI(api_key=LLM_API_KEY)
        elif LLM_SERVICE == "gemini":
            genai.configure(api_key=LLM_API_KEY)
            self.chat_client = None

    def get_category_from_path(self, file_path):
        try:
            relative_path = file_path.relative_to(self.root_dir)
            return (
                "Uncategorized"
                if str(relative_path.parent) == "."
                else relative_path.parts[0]
            )
        except ValueError:
            return "External"

    def extract_metadata_from_name(self, filename):
        match = self.audio_pattern.match(filename)
        if match:
            raw_ts = match.group(5)
            # Format: 20260109 -> 2026-01-09
            formatted_date = f"{raw_ts[:4]}-{raw_ts[4:6]}-{raw_ts[6:8]}"
            return {
                "employee_name": match.group(2) if match.group(2) else "Unknown",
                "extension": match.group(3),
                "phone_number": match.group(4),  # This is the 'Recipient'
                "timestamp_raw": raw_ts,
                "date_formatted": formatted_date,
            }
        return {}

    def format_timestamp(self, seconds: float) -> str:
        """Helper to convert float seconds to MM:SS format."""
        if seconds is None:
            return "00:00"
        mins = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{mins:02d}:{secs:02d}"

    async def analyze_full_transcript(self, full_text):
        """Quickly analyzes the whole call to get sentiment/purpose/etc."""
        prompt = f"""Analiza la siguiente transcripción y devuelve un objeto JSON con:
        - sentiment_score (1-10)
        - call_purpose (Ventas, Soporte, Facturación, Queja, Otro)
        - resolution_status (Resuelto, Pendiente)
        - summary (Resumen de 3-5 oraciones)
        - recommendation (Recomendación para incrementar la calidad de la llamada.)
        
        Texto: {full_text[:6000]}"""  # Limit text to stay within token bounds

        # Call your LLM (using the same logic as ChatAgent)
        response = await asyncio.to_thread(
            self.chat_client.chat.completions.create,
            model="gpt-4o-mini",  # Use a smaller/cheaper model for this
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": prompt}],
        )
        return json.loads(response.choices[0].message.content)

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

    async def index_file(self, file_path) -> bool:
        """Returns True when the file is fully indexed (or has nothing to index)."""
        print(f"Processing and Analyzing: {file_path.name}")
        try:
            result = self.converter.convert(file_path)
            # 1. Combine all text to get a full transcript for analysis
            full_transcript = " ".join([item.text for item in result.document.texts])

            # 2. Get the analysis metrics
            analysis = await self.analyze_full_transcript(full_transcript)

            file_meta = self.extract_metadata_from_name(file_path.name)
            category = self.get_category_from_path(file_path)

            chunks_to_insert = []
            combined_text = ""

            for item in result.document.texts:
                combined_text += item.text + " "
                if len(combined_text) >= 1200:
                    vector = self.embedder.get_embedding(combined_text)
                    if not vector:
                        # Keep the old chunks and retry on the next run instead of storing a partial call
                        print(f"Embedding failed for {file_path.name}; keeping old chunks.")
                        return False
                    chunks_to_insert.append(
                        {
                            "content": combined_text.strip(),
                            "embedding": vector,
                            "metadata": {
                                "filepath": db_key(file_path),
                                "filename": file_path.name,
                                "category": category,
                                **file_meta,
                                **analysis,  # <--- INJECT ANALYSIS INTO METADATA
                            },
                        }
                    )
                    combined_text = ""

            # 4. Handle remaining text
            if combined_text.strip():
                vector = self.embedder.get_embedding(combined_text)
                if not vector:
                    print(f"Embedding failed for {file_path.name}; keeping old chunks.")
                    return False
                chunks_to_insert.append(
                    {
                        "content": combined_text.strip(),
                        "embedding": vector,
                        "metadata": {"filepath": db_key(file_path), **file_meta},
                    }
                )

            # 5. Replace old entries only once the new chunks are ready
            if not self.delete_file(file_path):
                return False

            if chunks_to_insert:
                for i in range(0, len(chunks_to_insert), 10):
                    self.supabase.schema(self.db_schema).table(self.db_table).insert(
                        chunks_to_insert[i : i + 10]
                    ).execute()
                print(
                    f"Finished {file_path.name}: Created {len(chunks_to_insert)} larger chunks."
                )
            return True

        except Exception as e:
            print(f"Failed to process {file_path.name}: {e}")
            return False

    async def run_indexer(self, files_to_process=None):
        """Indexes the given files (or every local call). Returns the successfully indexed paths."""
        if files_to_process:
            files = files_to_process
        else:
            files = [
                f
                for f in self.root_dir.rglob("*.wav")
                if f.is_file() and self.audio_pattern.match(f.name)
            ]
        indexed = []
        for f in files:
            if await self.index_file(f):
                indexed.append(f)
        return indexed


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

    async def generate_response(self, query, message_history, context_chunks):
        # Format the context to help the AI infer roles
        context_text = ""
        for doc in context_chunks:
            meta = doc.metadata
            context_text += f"\n--- LLAMADA DE: {meta.get('employee_name')} (Ext: {meta.get('extension')}) ---\n"
            context_text += f"Fecha: {meta.get('date_formatted')}\n"
            context_text += f"Transcripción:\n{doc.page_content}\n"

        # 2. Build Messages for OpenAI
        # Start with the System Prompt

        system_prompt = """You are an expert quality assurance and conversation analist for Cooperativa Barcelona. 
        Your objective is to analyze audio transcriptions for the company and extract metrics in JSON format. You always answer in JSON format.
        
        Guidelines:
        1. ROLE INFERENCE: Even if the text has no tags, identify the 'Employee' (who offers help/services) and the 'Customer' (who requests help/information) from the context.
        2. LANGUAGE: Always respond in Spanish.
        3. ACCURACY: If the information is not in the context, use "Not available".
        4. FORMAT: Return ONLY a valid JSON object. No text before or after.
        5. If the user's input is a greeting, small talk, or a general question (like 'How are you?' or 'What is the capital of France?'), answer naturally and amicably without referencing documents.
        6. If the user asks a specific question about the conversations, calls, transcripts, or any other audio related information, use the provided Context to answer as detailed as possible.
        7. If the question requires audio related information but the information is NOT in the Context, politely say: "I'm sorry, I couldn't find that specific information in the audio conversations available to me." and provide the most relevant information available.
        8. If chunks of text are provided, use them to answer the question.
        9. Always maintain a polite and helpful tone.
        10. Always return a summary of the conversation, call, or transcript in the answer.
        
        

        METRICS TO EXTRACT:

        - 'answer': Direct response to the user's question.
        - 'sentiment_score': Number from 1 to 10 (1: very frustrated, 10: very satisfied).
        - 'call_purpose': Category (Technical Support, Billing, Complaint, Sales, General, Information).
        - 'resolution_status': Was the problem resolved? (Resolved / Pending / Unavailable).
        - 'action_items': List of pending tasks mentioned.
        - 'summary': A 3-5 sentence executive summary of the call.
        - 'recommendation': A recommendation given to increase sentiment_score.

        Desired JSON Output:
        {
            "answer": [Answer] or "No disponible",
            "conversation_date": [Conversation date],
            "employee_name": [Employee name],
            "extension": [Extension number],
            "tags": [Tags] or "No disponible",
            "summary": [Summary] or "No disponible"
            "sentiment_score": 0,
            "call_purpose": "...",
            "resolution_status": "...",
            "action_items": [],
            "recommendation": "..."
        }

        Example:
        {
            "answer": "The conversation between John Doe and Jane Smith was about the project XYZ...",
            "conversation_date": "2026-04-13",
            "employee_name": "John Doe",
            "extension": "1234",
            "tags": "project XYZ, conversation, call, transcript",
            "summary": "The conversation between John Doe and Jane Smith was about the project XYZ...",
            "sentiment_score": 0,
            "call_purpose": "...",
            "resolution_status": "...",
            "action_items": [],
            "recommendation": "..."
        }
        """

        full_prompt = f"Context:\n{context_text}\n\nQuestion: {query}"

        messages = [
            {
                "role": "system",
                "content": system_prompt + f"\nContexto de documentos:\n{context_text}",
            }
        ]

        # Add the conversation history
        for msg in message_history:
            role = "user" if isinstance(msg, HumanMessage) else "assistant"
            messages.append({"role": role, "content": msg.content})

        # 3. Call LLM
        if LLM_SERVICE == "openai":
            response = await asyncio.to_thread(
                self.chat_client.chat.completions.create,
                model="gpt-4o",
                response_format={"type": "json_object"},
                messages=messages,
            )
            return response.choices[0].message.content

        elif LLM_SERVICE == "gemini":
            # Run the asynchronous Gemini call in a background thread
            prompt = f"{system_prompt}\n\n{message_history}"
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
                print(
                    f"Agent (JSON):\n{json.dumps(parsed, indent=4, ensure_ascii=False)}"
                )
            except:
                print(f"Agent: {answer}")


# --- Scheduled Indexing Execution Flow ---
async def scheduled_audio_indexing(force=False, limit=None, folder_path=None):
    """Syncs SharePoint, indexes new/modified calls and drops removed ones. Returns a summary."""
    DOWNLOAD_DIR = data_dir() / "downloads_audio"

    # 1. Run SharePoint Sync
    syncer = SharePointSync(
        DOWNLOAD_DIR, force=force, limit=limit, folder_path=folder_path
    )
    updated_files = syncer.run()

    # 2. Run Indexer
    indexer = KnowledgeBaseIndexer(DOWNLOAD_DIR)

    indexed = await indexer.run_indexer(updated_files) if updated_files else []
    if not updated_files:
        print("No new files from SharePoint.")

    # 3. Only files that were indexed are recorded as synced
    for path in indexed:
        syncer.commit(path)

    # 4. Drop chunks of calls removed or moved in SharePoint
    deleted = syncer.apply_removals(indexer.delete_file)

    return {
        "synced": len(updated_files),
        "indexed": len(indexed),
        "failed": len(updated_files) - len(indexed),
        "deleted": deleted,
    }


# --- Execution ---
if __name__ == "__main__":
    summary = asyncio.run(scheduled_audio_indexing())
    print(summary)

    if not summary["synced"]:
        print("No new audio files. Running full scan...")
        asyncio.run(KnowledgeBaseIndexer(data_dir() / "downloads_audio").run_indexer())
