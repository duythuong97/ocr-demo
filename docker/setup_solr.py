#!/usr/bin/env python3
"""
Bootstrap a Solr core + schema for this project without Docker.

Usage:
    python setup_solr.py                          # default http://localhost:8983
    python setup_solr.py http://localhost:8983
    python setup_solr.py http://localhost:8983 my_core_name

Requires: requests  (pip install requests)
Solr version: 9.x
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

# Load .env so SOLR_URL is available as a default
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass  # dotenv optional; env vars from shell still work


def _parse_solr_url(solr_url: str) -> tuple[str, str]:
    """Split 'http://host:port/solr/core' → (base, core_name)."""
    parsed = urlparse(solr_url)
    parts = [p for p in parsed.path.split("/") if p]
    # Expected path: /solr/<core>
    if len(parts) >= 2 and parts[0] == "solr":
        core = parts[1]
        base = f"{parsed.scheme}://{parsed.netloc}"
        return base, core
    # Fallback: treat whole URL as base
    return solr_url.rstrip("/"), "documents"


_env_solr_url = os.getenv("SOLR_URL", "http://localhost:8983/solr/documents")
_default_base, _default_core = _parse_solr_url(_env_solr_url)

# ── Config ────────────────────────────────────────────────────────────────────
SOLR_BASE = sys.argv[1] if len(sys.argv) > 1 else _default_base
CORE_NAME = sys.argv[2] if len(sys.argv) > 2 else _default_core
SOLR_CORE = f"{SOLR_BASE}/solr/{CORE_NAME}"

# ── Helpers ───────────────────────────────────────────────────────────────────

def ok(label: str, res: requests.Response) -> None:
    body = res.json()
    if res.status_code >= 400 or body.get("responseHeader", {}).get("status", 0) != 0:
        # Ignore "already exists" errors for idempotent re-runs
        errors = body.get("error", {})
        msg = errors.get("msg", str(body)) if errors else str(body)
        if "already exists" in msg.lower() or "duplicate" in msg.lower():
            print(f"  [skip] {label}: already exists")
        else:
            print(f"  [FAIL] {label}: {msg}")
            sys.exit(1)
    else:
        print(f"  [ ok ] {label}")


def schema_post(payload: dict) -> requests.Response:
    return requests.post(
        f"{SOLR_CORE}/schema",
        headers={"Content-Type": "application/json"},
        data=json.dumps(payload),
        timeout=30,
    )


def config_post(payload: dict) -> requests.Response:
    return requests.post(
        f"{SOLR_CORE}/config",
        headers={"Content-Type": "application/json"},
        data=json.dumps(payload),
        timeout=30,
    )


# ── Step 1: Create core ───────────────────────────────────────────────────────

print(f"\n=== Setting up Solr core '{CORE_NAME}' on {SOLR_BASE} ===\n")

print("1. Creating core...")
res = requests.get(
    f"{SOLR_BASE}/solr/admin/cores",
    params={"action": "CREATE", "name": CORE_NAME, "configSet": "_default"},
    timeout=30,
)
ok("create core", res)
time.sleep(1)  # let Solr finish initialising

# ── Step 2: Field types ───────────────────────────────────────────────────────

print("\n2. Registering field types...")

# text_general: standard tokenizer, lowercase, stop words, synonyms on query
ok("replace text_general", schema_post({
    "replace-field-type": {
        "name": "text_general",
        "class": "solr.TextField",
        "positionIncrementGap": "100",
        "multiValued": True,
        "indexAnalyzer": {
            "tokenizer": {"class": "solr.StandardTokenizerFactory"},
            "filters": [
                {"class": "solr.StopFilterFactory", "words": "stopwords.txt", "ignoreCase": "true"},
                {"class": "solr.LowerCaseFilterFactory"},
            ],
        },
        "queryAnalyzer": {
            "tokenizer": {"class": "solr.StandardTokenizerFactory"},
            "filters": [
                {"class": "solr.StopFilterFactory", "words": "stopwords.txt", "ignoreCase": "true"},
                {"class": "solr.SynonymGraphFilterFactory", "synonyms": "synonyms.txt", "ignoreCase": "true", "expand": "true"},
                {"class": "solr.LowerCaseFilterFactory"},
            ],
        },
    }
}))

# text_ocr: tolerant of diacritics / OCR noise, strips accents
ok("add text_ocr", schema_post({
    "add-field-type": {
        "name": "text_ocr",
        "class": "solr.TextField",
        "positionIncrementGap": "100",
        "indexAnalyzer": {
            "tokenizer": {"class": "solr.StandardTokenizerFactory"},
            "filters": [
                {"class": "solr.LowerCaseFilterFactory"},
                {"class": "solr.ASCIIFoldingFilterFactory", "preserveOriginal": "true"},
                {"class": "solr.StopFilterFactory", "words": "stopwords.txt", "ignoreCase": "true"},
            ],
        },
        "queryAnalyzer": {
            "tokenizer": {"class": "solr.StandardTokenizerFactory"},
            "filters": [
                {"class": "solr.LowerCaseFilterFactory"},
                {"class": "solr.ASCIIFoldingFilterFactory", "preserveOriginal": "true"},
                {"class": "solr.StopFilterFactory", "words": "stopwords.txt", "ignoreCase": "true"},
            ],
        },
    }
}))

# text_code: splits camelCase / underscore / numbers for source code
ok("add text_code", schema_post({
    "add-field-type": {
        "name": "text_code",
        "class": "solr.TextField",
        "positionIncrementGap": "100",
        "indexAnalyzer": {
            "tokenizer": {"class": "solr.WhitespaceTokenizerFactory"},
            "filters": [
                {
                    "class": "solr.WordDelimiterGraphFilterFactory",
                    "generateWordParts": "1", "generateNumberParts": "1",
                    "catenateWords": "1", "catenateNumbers": "1", "catenateAll": "0",
                    "splitOnCaseChange": "1", "splitOnNumerics": "1", "preserveOriginal": "1",
                },
                {"class": "solr.LowerCaseFilterFactory"},
                {"class": "solr.FlattenGraphFilterFactory"},
            ],
        },
        "queryAnalyzer": {
            "tokenizer": {"class": "solr.WhitespaceTokenizerFactory"},
            "filters": [
                {
                    "class": "solr.WordDelimiterGraphFilterFactory",
                    "generateWordParts": "1", "generateNumberParts": "1",
                    "catenateWords": "0", "catenateNumbers": "0", "catenateAll": "0",
                    "splitOnCaseChange": "1", "splitOnNumerics": "1", "preserveOriginal": "1",
                },
                {"class": "solr.LowerCaseFilterFactory"},
                {"class": "solr.FlattenGraphFilterFactory"},
            ],
        },
    }
}))

# ── Step 3: Fields ────────────────────────────────────────────────────────────

print("\n3. Adding fields...")

FIELDS = [
    # Core content
    {"name": "title",                "type": "text_general", "indexed": True,  "stored": True},
    {"name": "content",              "type": "text_general", "indexed": True,  "stored": True},
    {"name": "image_ocr_text",       "type": "text_ocr",     "indexed": True,  "stored": True},
    {"name": "source_code_content",  "type": "text_code",    "indexed": True,  "stored": True},
    # Metadata
    {"name": "language",       "type": "string", "indexed": True,  "stored": True,  "docValues": True},
    {"name": "file_type",      "type": "string", "indexed": True,  "stored": True,  "docValues": True},
    {"name": "file_path",      "type": "string", "indexed": True,  "stored": True,  "docValues": True},
    {"name": "author",         "type": "string", "indexed": True,  "stored": True,  "docValues": True},
    {"name": "repository",     "type": "string", "indexed": True,  "stored": True,  "docValues": True},
    {"name": "last_modified",  "type": "pdate",  "indexed": True,  "stored": True,  "docValues": True},
    {"name": "url",            "type": "string", "indexed": False, "stored": True},
    {"name": "repository_path","type": "string", "indexed": False, "stored": True},
    {"name": "tags",           "type": "string", "indexed": True,  "stored": True,  "multiValued": True, "docValues": True},
]

for f in FIELDS:
    ok(f"add field '{f['name']}'", schema_post({"add-field": f}))

# ── Step 4: Dynamic fields ────────────────────────────────────────────────────

print("\n4. Adding dynamic fields...")

DYNAMIC_FIELDS = [
    {"name": "*_i",  "type": "pint",         "indexed": True, "stored": True},
    {"name": "*_s",  "type": "string",        "indexed": True, "stored": True},
    {"name": "*_l",  "type": "plong",         "indexed": True, "stored": True},
    {"name": "*_t",  "type": "text_general",  "indexed": True, "stored": True},
    {"name": "*_b",  "type": "boolean",       "indexed": True, "stored": True},
    {"name": "*_f",  "type": "pfloat",        "indexed": True, "stored": True},
    {"name": "*_d",  "type": "pdouble",       "indexed": True, "stored": True},
    {"name": "*_dt", "type": "pdate",         "indexed": True, "stored": True},
]

for f in DYNAMIC_FIELDS:
    ok(f"add dynamic field '{f['name']}'", schema_post({"add-dynamic-field": f}))

# ── Step 5: Request handler (edismax + highlighting + facets) ─────────────────

print("\n5. Configuring /select request handler...")

ok("update /select handler", config_post({
    "update-requesthandler": {
        "name": "/select",
        "class": "solr.SearchHandler",
        "defaults": {
            "echoParams": "explicit",
            "rows": 10,
            "df": "content",
            "defType": "edismax",
            "qf": "title^5 content^1 author^2 file_path^1",
            "mm": "1",
            "hl": "true",
            "hl.fl": "title,content",
            "hl.snippets": "3",
            "hl.fragsize": "150",
            "hl.simple.pre": "<mark>",
            "hl.simple.post": "</mark>",
        },
        "components": ["query", "facet", "mlt", "highlight", "debug"],
    }
}))

# ── Done ──────────────────────────────────────────────────────────────────────

print(f"\n=== Done! Core '{CORE_NAME}' is ready at {SOLR_CORE} ===\n")
