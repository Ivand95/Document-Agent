import os
import json
import re
import asyncio
from pathlib import Path
from dotenv import load_dotenv


# --- Indexing imports ---
from supabase import create_client, Client
from docling.document_converter import AudioFormatOption, DocumentConverter
from docling.datamodel.base_models import InputFormat
from docling.datamodel import asr_model_specs
from docling.datamodel.pipeline_options import AsrPipelineOptions
from docling.pipeline.asr_pipeline import AsrPipeline
import openai
import google.generativeai as genai

from sharepoint_sync import SharePointSyncBase, data_dir, db_key
from config import (
    LIGHT_MODEL,
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

# Bump when chunking changes: calls indexed with another version are re-indexed on the next sync
CHUNKING_VERSION = "2"
CHUNK_CHARS = 1200


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
            version=CHUNKING_VERSION,
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
            model=LIGHT_MODEL,  # Use a smaller/cheaper model for this
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

    def transcript_items(self, doc):
        """[(text, start_seconds, end_seconds)] for every non-empty ASR segment."""
        items = []
        for t in doc.texts:
            text = t.text.strip()
            if not text:
                continue
            track = next(
                (s for s in (t.source or []) if getattr(s, "start_time", None) is not None),
                None,
            )
            items.append(
                (text, track.start_time if track else None, track.end_time if track else None)
            )
        return items

    def build_chunks(self, items, base_meta):
        """Groups segments into ~CHUNK_CHARS chunks. Every chunk gets the same call-level metadata
        plus its own position and time range. `content` keeps [MM:SS] markers for the LLM;
        `embedding_text` is the plain speech."""
        groups, current, size = [], [], 0
        for item in items:
            current.append(item)
            size += len(item[0]) + 1
            if size >= CHUNK_CHARS:
                groups.append(current)
                current, size = [], 0
        if current:
            groups.append(current)

        rows = []
        for i, group in enumerate(groups):
            lines = [
                f"[{self.format_timestamp(start)}] {text}" if start is not None else text
                for text, start, _ in group
            ]
            metadata = {**base_meta, "chunk_index": i, "chunk_total": len(groups)}
            starts = [s for _, s, _ in group if s is not None]
            ends = [e for _, _, e in group if e is not None]
            if starts and ends:
                metadata.update(
                    start_time=starts[0],
                    end_time=ends[-1],
                    start=self.format_timestamp(starts[0]),
                    end=self.format_timestamp(ends[-1]),
                )
            rows.append(
                {
                    "content": "\n".join(lines),
                    "embedding_text": " ".join(text for text, _, _ in group),
                    "metadata": metadata,
                }
            )
        return rows

    async def index_file(self, file_path) -> bool:
        """Returns True when the file is fully indexed (or has nothing to index)."""
        print(f"Processing and Analyzing: {file_path.name}")
        try:
            result = self.converter.convert(file_path)
            items = self.transcript_items(result.document)
            if not items:
                print(f"No speech found in {file_path.name}")
                return self.delete_file(file_path)

            # 1. Analyze the whole call to get sentiment/purpose/etc.
            analysis = await self.analyze_full_transcript(
                " ".join(text for text, _, _ in items)
            )

            # 2. Same metadata on every chunk of the call
            base_meta = {
                "filepath": db_key(file_path),
                "filename": file_path.name,
                "category": self.get_category_from_path(file_path),
                **self.extract_metadata_from_name(file_path.name),
                **analysis,
            }
            rows = self.build_chunks(items, base_meta)

            # 3. Embed in batches
            vectors = self.embedder.get_embeddings([r["embedding_text"] for r in rows])
            if vectors is None:
                # Keep the old chunks and retry on the next run instead of storing a partial call
                print(f"Embedding failed for {file_path.name}; keeping old chunks.")
                return False

            chunks_to_insert = [
                {
                    "content": r["content"],
                    "metadata": r["metadata"],
                    "embedding": vector,
                }
                for r, vector in zip(rows, vectors)
            ]

            # 4. Replace old entries only once the new chunks are ready
            if not self.delete_file(file_path):
                return False

            for i in range(0, len(chunks_to_insert), 10):
                self.supabase.schema(self.db_schema).table(self.db_table).insert(
                    chunks_to_insert[i : i + 10]
                ).execute()
            print(f"Finished {file_path.name}: Created {len(chunks_to_insert)} chunks.")
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
