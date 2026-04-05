"""
Central configuration for the Solr Search Web App.
Environment variables (from .env or shell) override the defaults below.
Copy .env.example → .env and edit for your environment.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

# ── Solr Connection ────────────────────────────────────────────────────────────
SOLR_URL = os.getenv("SOLR_URL", "http://localhost:8983/solr/documents")
SOLR_TIMEOUT = int(os.getenv("SOLR_TIMEOUT", "10"))

# ── Default Search Behaviour ───────────────────────────────────────────────────
DEFAULT_ROWS = int(os.getenv("DEFAULT_ROWS", "10"))
DEFAULT_OPERATOR = os.getenv("DEFAULT_OPERATOR", "OR")  # "OR" | "AND"
DEFAULT_PARSER = os.getenv("DEFAULT_PARSER", "edismax")  # "lucene" | "edismax"
APP_PREFIX = os.getenv("APP_PREFIX", "ocr-search")
SIMULATE_SUBDIRECTORY = os.getenv("SIMULATE_SUBDIRECTORY", "true").lower() == "true"

# ── Semantic / Qdrant ─────────────────────────────────────────────────────────
SEMANTIC_MODEL = os.getenv("SEMANTIC_MODEL", "BAAI/bge-m3")
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "semantic_chunks")
QDRANT_STORAGE_PATH = Path(
    os.getenv(
        "QDRANT_STORAGE_PATH",
        str(Path(__file__).resolve().parent / "data" / "qdrant" / "storage"),
    )
)
SEMANTIC_MIN_SCORE = float(os.getenv("SEMANTIC_MIN_SCORE", "0.45"))
SEMANTIC_MAX_TEXT_CHARS = int(os.getenv("SEMANTIC_MAX_TEXT_CHARS", "250000"))
SEMANTIC_MAX_CHUNKS_PER_FILE = int(os.getenv("SEMANTIC_MAX_CHUNKS_PER_FILE", "120"))
SEMANTIC_SKIP_FILE_TYPES = {
    v.strip().lstrip(".").lower()
    for v in os.getenv("SEMANTIC_SKIP_FILE_TYPES", "").split(",")
    if v.strip()
}

# ── Proxy Chat ───────────────────────────────────────────────────────────────
PROXY_CHAT_URL = os.getenv("PROXY_CHAT_URL", "http://localhost:3000/api/lmstudio/chat")
PROXY_CHAT_MODEL_ID = os.getenv("PROXY_CHAT_MODEL_ID", "qwen/qwen3-14b")
PROXY_CHAT_MODEL_NAME = os.getenv("PROXY_CHAT_MODEL_NAME", "qwen/qwen3-14b")
PROXY_CHAT_TIMEOUT = int(os.getenv("PROXY_CHAT_TIMEOUT", "120"))
RAG_TOP_K = int(os.getenv("RAG_TOP_K", "5"))
RAG_SEARCH_MODE = os.getenv("RAG_SEARCH_MODE", "hybrid")  # hybrid | semantic | fulltext
RAG_SYSTEM_PROMPT = os.getenv(
    "RAG_SYSTEM_PROMPT",
    "You are a helpful assistant. Answer ONLY based on the context documents provided below. "
    "Do NOT use your own training knowledge or make up information that is not present in the context. "
    "Cite the source file name when referencing specific content. "
    "If the context documents do not contain the answer, respond with: "
    "'I could not find relevant information in the indexed documents.'",
)

# ── Flask ─────────────────────────────────────────────────────────────────────
FLASK_PORT = int(os.getenv("FLASK_PORT", "5100"))
SHUTDOWN_FORCE_EXIT_SECONDS = int(os.getenv("SHUTDOWN_FORCE_EXIT_SECONDS", "8"))

# ── Result Link Behaviour ─────────────────────────────────────────────────────
LOCAL_FILE_ROOT = Path(__file__).resolve().parent


# ── Schema Field Names  ────────────────────────────────────────────────────────
# Adjust these to match your actual Solr schema field names.
FIELD_ID = "id"
FIELD_TITLE = "title"
FIELD_CONTENT = "content"
FIELD_OCR = "image_ocr_text"  # OCR-scanned pages/images
FIELD_CODE = "source_code_content"  # Source code (camelCase-aware)
FIELD_FILE_TYPE = "file_type"
FIELD_FILE_PATH = "file_path"
FIELD_REPOSITORY = "repository"
FIELD_REPOSITORY_PATH = "repository_path"  # base browse URL stored per-document
FIELD_AUTHOR = "author"
FIELD_DATE = "last_modified"
FIELD_URL = "url"

# Fields used for keyword searching (edismax qf) with boost weights
QUERY_FIELDS = {
    "title": 5.0,  # filename / document title
    "source_code_content": 1.5,  # code-aware (camelCase split) → precise
    "content": 1.0,  # general text (docs, markdown)
    "image_ocr_text": 0.6,  # OCR text — noisy, lower trust
    "author": 2.0,
    "file_path": 1.0,
}

# ── Facet Fields ───────────────────────────────────────────────────────────────
FACET_FIELDS = [FIELD_REPOSITORY, FIELD_FILE_TYPE]

# ── Highlight Settings ─────────────────────────────────────────────────────────
HL_FIELDS = f"{FIELD_TITLE},{FIELD_CONTENT},{FIELD_CODE},{FIELD_OCR}"
HL_SNIPPETS = 3
HL_FRAG_SIZE = 150
HL_PRE_TAG = "<mark>"
HL_POST_TAG = "</mark>"

# ── File Type Options ─────────────────────────────────────────────────────────
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

# ── Sort Options ──────────────────────────────────────────────────────────────
SORT_OPTIONS = [
    {"value": "score desc", "label": "Relevance"},
    {"value": "last_modified desc", "label": "Newest First"},
    {"value": "last_modified asc", "label": "Oldest First"},
    {"value": "title asc", "label": "Title A→Z"},
    {"value": "title desc", "label": "Title Z→A"},
]
