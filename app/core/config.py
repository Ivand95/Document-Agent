import os
import unicodedata
from supabase import create_client, Client
import openai
import google.generativeai as genai
from dotenv import load_dotenv


load_dotenv()

# --- Configuration ---
LLM_SERVICE = os.getenv("LLM_SERVICE", "openai").lower()
LLM_API_KEY = os.getenv("LLM_SERVICE_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
SUPABASE_SCHEMA = os.getenv("SUPABASE_SCHEMA", "documents_rag")
SUPABASE_TABLE = os.getenv("SUPABASE_TABLE", "chunks")
SUPABASE_AUDIO_TABLE = os.getenv("SUPABASE_AUDIO_TABLE", "audio_chunks")

# Embeddings are set apart from chat: changing them means re-indexing everything (the vectors in pgvector
# come from this model, and their size is fixed in db_setup.EMBEDDING_DIM). OpenRouter can't serve them.
EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER", LLM_SERVICE).lower()
EMBEDDING_MODEL = os.getenv(
    "EMBEDDING_MODEL", "models/embedding-001" if EMBEDDING_PROVIDER == "gemini" else "text-embedding-3-small"
)
if EMBEDDING_PROVIDER not in ("openai", "gemini"):
    raise ValueError(f"EMBEDDING_PROVIDER must be openai or gemini, not '{EMBEDDING_PROVIDER}'")

# Models are configurable so they can be upgraded without code changes.
# The core agents only use OpenAI models (core/llm.py refuses others).
CHAT_MODEL = os.getenv("CHAT_MODEL", "gpt-4o")  # final answers
LIGHT_MODEL = os.getenv("LIGHT_MODEL", "gpt-4o-mini")  # query rewriting, reranking, call analysis

# Retrieval knobs (tune with the evals in evals/)
RETRIEVAL_K = int(os.getenv("RETRIEVAL_K", "8"))  # chunks given to the LLM
RETRIEVAL_CANDIDATES = int(os.getenv("RETRIEVAL_CANDIDATES", "20"))  # chunks fetched before reranking
RETRIEVAL_MATCH_THRESHOLD = float(os.getenv("RETRIEVAL_MATCH_THRESHOLD", "0.4"))
AUDIO_MATCH_THRESHOLD = float(os.getenv("AUDIO_MATCH_THRESHOLD", "0.2"))
RERANK_ENABLED = os.getenv("RERANK_ENABLED", "true").lower() == "true"
HISTORY_MAX_MESSAGES = int(os.getenv("HISTORY_MAX_MESSAGES", "8"))  # messages sent to the LLM
ASR_MODEL = os.getenv("ASR_MODEL", "medium")  # Whisper size used to transcribe calls
INDEXING_PAUSE_SECONDS = float(os.getenv("INDEXING_PAUSE_SECONDS", "1"))  # idle time between files, so the DB keeps serving searches

DEPARTMENT_LIST = {
    "Gestion Humana": "HR",
    "Tecnología": "IT",
    "Contabilidad": "FI",
    "Administrativo": "ADM",
    "Cumplimiento": "CU",
    "Marketing": "MKT",
    "Comercial": "COM",
    "Creditos y Cobros": "CC",
    "Atencion al Socio": "COM",
    "Auditoria": "AUD",
}


EMBED_BATCH_SIZE = 64


# --- Helper: Embedding Generator Factory (from your initial script) ---
class EmbeddingService:
    def __init__(self):
        self.service = EMBEDDING_PROVIDER
        self.model = EMBEDDING_MODEL
        self.api_key = LLM_API_KEY

        if self.service == "openai":
            self.client = openai.OpenAI(api_key=self.api_key)
        elif self.service == "gemini":
            genai.configure(api_key=self.api_key)

    def get_embedding(self, text):
        text = text.replace("\n", " ")
        try:
            if self.service == "openai":
                response = self.client.embeddings.create(input=[text], model=self.model)
                return response.data[0].embedding
            elif self.service == "gemini":
                result = genai.embed_content(
                    model=self.model,
                    content=text,
                    task_type="retrieval_query",
                )
                return result["embedding"]
            else:
                raise ValueError("Unsupported EMBEDDING_PROVIDER")
        except Exception as e:
            print(f"Error generating embedding: {e}")
            return None


    def get_embeddings(self, texts):
        """Embeds many documents in few requests.

        Returns one vector per text, or None if any batch failed so the caller can
        keep the previously stored chunks instead of a partial file.
        """
        vectors = []
        for i in range(0, len(texts), EMBED_BATCH_SIZE):
            batch = [t.replace("\n", " ") for t in texts[i : i + EMBED_BATCH_SIZE]]
            try:
                if self.service == "openai":
                    response = self.client.embeddings.create(input=batch, model=self.model)
                    ordered = sorted(response.data, key=lambda d: d.index)
                    vectors.extend(d.embedding for d in ordered)
                elif self.service == "gemini":
                    result = genai.embed_content(
                        model=self.model,
                        content=batch,
                        task_type="retrieval_document",
                    )
                    vectors.extend(result["embedding"])
                else:
                    raise ValueError("Unsupported EMBEDDING_PROVIDER")
            except Exception as e:
                print(f"Error generating embeddings: {e}")
                return None
        return vectors


# --- Helper: Department List Dictionary ---

# Category code -> label shown to users in references
CATEGORY_LABELS = {
    "HR": "Gestión Humana",
    "IT": "Tecnología",
    "FI": "Contabilidad",
    "ADM": "Administrativo",
    "CU": "Cumplimiento",
    "MKT": "Marketing",
    "COM": "Comercial",
    "CC": "Créditos y Cobros",
    "AUD": "Auditoría",
    "OTROS": "General",
    "STAFF GERENCIAL": "Staff Gerencial",
}


def _plain(text: str) -> str:
    """Lowercase without accents, so "Tecnología" and "tecnologia" are the same department."""
    decomposed = unicodedata.normalize("NFD", text or "")
    return "".join(c for c in decomposed if not unicodedata.combining(c)).strip().casefold()


def department_options() -> list[dict]:
    """Departments a call folder can be assigned to: code + label."""
    codes = list(dict.fromkeys(DEPARTMENT_LIST.values())) + ["OTROS"]
    return [{"code": c, "label": CATEGORY_LABELS.get(c, c)} for c in codes]


def get_department_categories(department: str, position: str) -> list[str]:
    """The rule used before RBAC. The API now takes categories from RBAC (seeded with this same rule in
    migration 0005); this stays for the local CLI and evals, and as the reference the seed must match."""
    if position and position.startswith("GR -"):
        return list(DEPARTMENT_LIST.values()) + ["OTROS", "STAFF GERENCIAL"]
    elif department not in DEPARTMENT_LIST:
        return ["OTROS"]
    return [DEPARTMENT_LIST[department], "OTROS"]


# --- Initialize Global Supabase Client and Embedder Instance ---
# These will be used by your `custom_supabase_search` function
global_supabase_client: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
global_embedding_service_instance = EmbeddingService()
