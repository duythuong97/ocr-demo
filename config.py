"""
Central configuration for the Solr Search Web App.
Edit the values below to match your Solr setup.
"""

from pathlib import Path

# ── Solr Connection ────────────────────────────────────────────────────────────
SOLR_URL = (
    "http://192.168.1.80:8983/solr/documents"  # Change to your core/collection URL
)
SOLR_TIMEOUT = 10  # seconds

# ── Default Search Behaviour ───────────────────────────────────────────────────
DEFAULT_ROWS = 10  # results per page
DEFAULT_OPERATOR = "OR"  # "OR" | "AND"
DEFAULT_PARSER = "edismax"  # "lucene" | "edismax"
APP_PREFIX = "ocr-search"  # e.g. "ocr-search" for the sub-directory name
SIMULATE_SUBDIRECTORY = True  # Set to False in Production/IIS

# ── Result Link Behaviour ─────────────────────────────────────────────────────
# Relative file paths from Solr will be resolved against this folder when
# building a local `file://` link.
LOCAL_FILE_ROOT = Path(__file__).resolve().parent


# ── Schema Field Names  ────────────────────────────────────────────────────────
# Adjust these to match your actual Solr schema field names.
FIELD_ID = "id"
FIELD_TITLE = "title"
FIELD_CONTENT = "content"
FIELD_OCR = "image_orc_text"  # OCR-scanned pages/images
FIELD_CODE = "source_code_content"  # Source code (camelCase-aware)
FIELD_LANGUAGE = "language"
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
    "image_orc_text": 0.6,  # OCR text — noisy, lower trust
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

# ── Language Options  ─────────────────────────────────────────────────────────
LANGUAGES = [
    {"value": "", "label": "All Languages"},
    {"value": "en", "label": "English"},
    {"value": "ja", "label": "日本語"},
    {"value": "vi", "label": "Tiếng Việt"},
]

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
