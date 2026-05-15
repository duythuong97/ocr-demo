import os
from pathlib import Path

from dotenv import load_dotenv

_base = Path(__file__).resolve().parent
load_dotenv(_base / ".env.defaults")       # committed defaults — loaded first
load_dotenv(_base / ".env", override=True) # host secrets — overrides defaults


def _require(key: str) -> str:
    val = os.getenv(key, "").strip()
    if not val:
        raise ValueError(
            f"Required environment variable '{key}' is not set. "
            f"Add it to your .env file."
        )
    return val


# ── Solr ──────────────────────────────────────────────────────────────────────
SOLR_URL     = _require("SOLR_URL")
SOLR_TIMEOUT = int(_require("SOLR_TIMEOUT"))

# ── Search ────────────────────────────────────────────────────────────────────
DEFAULT_ROWS          = int(_require("DEFAULT_ROWS"))
DEFAULT_OPERATOR      = _require("DEFAULT_OPERATOR")
DEFAULT_PARSER        = _require("DEFAULT_PARSER")
APP_PREFIX            = _require("APP_PREFIX")
SIMULATE_SUBDIRECTORY = _require("SIMULATE_SUBDIRECTORY").lower() == "true"

# ── Qdrant / Embeddings ───────────────────────────────────────────────────────
EMBEDDER_URL                 = _require("EMBEDDER_URL")
SEMANTIC_MODEL               = _require("SEMANTIC_MODEL")
QDRANT_URL                   = _require("QDRANT_URL")
QDRANT_COLLECTION            = _require("QDRANT_COLLECTION")
SEMANTIC_MIN_SCORE           = float(_require("SEMANTIC_MIN_SCORE"))
SEMANTIC_MAX_TEXT_CHARS      = int(_require("SEMANTIC_MAX_TEXT_CHARS"))
SEMANTIC_MAX_CHUNKS_PER_FILE = int(_require("SEMANTIC_MAX_CHUNKS_PER_FILE"))
EMBED_BATCH_SIZE             = int(_require("EMBED_BATCH_SIZE"))
EMBED_TIMEOUT                = int(_require("EMBED_TIMEOUT"))
SEMANTIC_SKIP_FILE_TYPES     = {
    v.strip().lstrip(".").lower()
    for v in os.getenv("SEMANTIC_SKIP_FILE_TYPES", "").split(",")
    if v.strip()
}

# ── Neo4j ─────────────────────────────────────────────────────────────────────
GRAPH_ENABLED        = _require("GRAPH_ENABLED").lower() == "true"
NEO4J_URL            = _require("NEO4J_URL")      if GRAPH_ENABLED else os.getenv("NEO4J_URL", "")
NEO4J_USER           = _require("NEO4J_USER")     if GRAPH_ENABLED else os.getenv("NEO4J_USER", "")
NEO4J_PASSWORD       = _require("NEO4J_PASSWORD") if GRAPH_ENABLED else os.getenv("NEO4J_PASSWORD", "")
NEO4J_DATABASE       = _require("NEO4J_DATABASE") if GRAPH_ENABLED else os.getenv("NEO4J_DATABASE", "")
GRAPH_RULES_PATH     = _require("GRAPH_RULES_PATH") if GRAPH_ENABLED else os.getenv("GRAPH_RULES_PATH", "")
AGENT_MAX_ITERATIONS = int(_require("AGENT_MAX_ITERATIONS")) if GRAPH_ENABLED else 0

# ── LLM ───────────────────────────────────────────────────────────────────────
LLM_BASE_URL              = _require("LLM_BASE_URL")
LLM_MODEL                 = _require("LLM_MODEL")
LLM_TIMEOUT               = int(_require("LLM_TIMEOUT"))
RAG_TOP_K                 = int(_require("RAG_TOP_K"))
RAG_CONTEXT_CHARS_PER_DOC = int(_require("RAG_CONTEXT_CHARS_PER_DOC"))
RAG_SYSTEM_PROMPT         = _require("RAG_SYSTEM_PROMPT")

# ── Retrieval ─────────────────────────────────────────────────────────────────
RETRIEVAL_RRF_K = int(_require("RETRIEVAL_RRF_K"))

# ── Reranker ─────────────────────────────────────────────────────────────────
RERANKER_URL            = _require("RERANKER_URL")
RERANKER_MODEL          = _require("RERANKER_MODEL")
RERANKER_LISTWISE_BATCH = int(_require("RERANKER_LISTWISE_BATCH"))

# ── Database ─────────────────────────────────────────────────────────────────
DB_URL = _require("DB_URL")

# ── OCR ───────────────────────────────────────────────────────────────────────
OCR_LANG         = _require("OCR_LANG")
PDF_OCR_FALLBACK = _require("PDF_OCR_FALLBACK").lower() == "true"

# ── Flask ─────────────────────────────────────────────────────────────────────
FLASK_PORT                  = int(_require("FLASK_PORT"))
SHUTDOWN_FORCE_EXIT_SECONDS = int(_require("SHUTDOWN_FORCE_EXIT_SECONDS"))

LOCAL_FILE_ROOT = Path(__file__).resolve().parent

# ── Solr Schema Fields ────────────────────────────────────────────────────────
FIELD_ID = "id"
FIELD_TITLE = "title"
FIELD_CONTENT = "content"
FIELD_OCR = "image_ocr_text"
FIELD_CODE = "source_code_content"
FIELD_FILE_TYPE = "file_type"
FIELD_FILE_PATH = "file_path"
FIELD_REPOSITORY = "repository"
FIELD_REPOSITORY_PATH = "repository_path"
FIELD_AUTHOR = "author"
FIELD_DATE = "last_modified"
FIELD_URL = "url"

QUERY_FIELDS = {
    "title": 5.0,
    "title_ja": 4.0,
    "source_code_content": 1.5,
    "content": 1.0,
    "content_ja": 0.9,
    "image_ocr_text": 0.6,
    "author": 2.0,
    "file_path": 1.0,
}

FACET_FIELDS = [FIELD_REPOSITORY, FIELD_FILE_TYPE]

HL_FIELDS = f"{FIELD_TITLE},{FIELD_CONTENT},{FIELD_CODE},{FIELD_OCR}"
HL_SNIPPETS = 3
HL_FRAG_SIZE = 400
HL_PRE_TAG = "<mark>"
HL_POST_TAG = "</mark>"

FILE_TYPES = [
    {"value": "", "label": "All Types"},
    {"value": "py", "label": "Python"},
    {"value": "js", "label": "JavaScript"},
    {"value": "ts", "label": "TypeScript"},
    {"value": "java", "label": "Java"},
    {"value": "go", "label": "Go"},
    {"value": "md", "label": "Markdown"},
    {"value": "txt", "label": "Text"},
    {"value": "pdf", "label": "PDF"},
    {"value": "html", "label": "HTML"},
    {"value": "rst", "label": "RST"},
]

SORT_OPTIONS = [
    {"value": "score desc", "label": "Relevance"},
    {"value": "last_modified desc", "label": "Newest First"},
    {"value": "last_modified asc", "label": "Oldest First"},
    {"value": "title asc", "label": "Title A→Z"},
    {"value": "title desc", "label": "Title Z→A"},
]
