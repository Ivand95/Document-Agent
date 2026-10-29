import os
import json
import asyncio
from pathlib import Path
from dotenv import load_dotenv

# --- Indexing imports ---
from supabase import create_client, Client
from docling.document_converter import DocumentConverter
from docling.chunking import HybridChunker  # specific docling chunker
from docling_core.transforms.chunker.tokenizer.openai import OpenAITokenizer
import tiktoken

from sharepoint_sync import SharePointSyncBase, data_dir, db_key
from config import (
    global_embedding_service_instance,
    SUPABASE_SCHEMA,
    SUPABASE_KEY,
    SUPABASE_URL,
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


# Bump when chunking changes: files indexed with another version are re-indexed on the next sync
CHUNKING_VERSION = "2"
CHUNK_MAX_TOKENS = 512
MIN_CHUNK_CHARS = 30


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
            version=CHUNKING_VERSION,
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
        # Hybrid chunks respect headings and tables and stay under the embedding token budget
        self.chunker = HybridChunker(
            tokenizer=OpenAITokenizer(
                tokenizer=tiktoken.get_encoding("cl100k_base"),  # text-embedding-3-*
                max_tokens=CHUNK_MAX_TOKENS,
            )
        )

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

    def build_chunks(self, file_path):
        """Converts a file into chunks ready to embed (no network calls besides Docling models).

        Each chunk carries its heading trail and pages; `embedding_text` also gets the document
        title so short chunks still embed with their context.
        """
        doc = self.converter.convert(file_path).document
        category = self.get_category_from_path(file_path)

        rows = []
        for chunk in self.chunker.chunk(dl_doc=doc):
            if len(chunk.text.strip()) < MIN_CHUNK_CHARS:
                continue

            content = self.chunker.contextualize(chunk)  # headings + body
            headings = list(chunk.meta.headings or [])
            pages = sorted(
                {p.page_no for item in chunk.meta.doc_items for p in item.prov}
            )

            metadata = {
                "filepath": db_key(file_path),
                "filename": file_path.name,
                "category": category,
                "chunk_index": len(rows),
            }
            if headings:
                metadata["headings"] = headings
                metadata["section"] = " > ".join(headings)
            if pages:  # formats without pages (docx, xlsx) have none
                metadata["page_no"] = pages[0]
                metadata["pages"] = pages

            rows.append(
                {
                    "content": content,
                    "embedding_text": f"{file_path.stem}\n{content}",
                    "metadata": metadata,
                }
            )
        return rows

    def index_file(self, file_path) -> bool:
        """Returns True when the file is fully indexed (or has nothing to index)."""
        print(f"Processing with Docling: {file_path.name}")

        try:
            # 1. Convert + chunk
            rows = self.build_chunks(file_path)

            # 2. Embed in batches
            vectors = self.embedder.get_embeddings([r["embedding_text"] for r in rows])
            if vectors is None:
                # Keep the old chunks and retry on the next run instead of storing a partial file
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

            # 3. Replace old entries only once the new chunks are ready
            if not self.delete_file(file_path):
                return False

            # 4. Batch Insert
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
