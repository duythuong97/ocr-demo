"""
Flask Solr Search Application
Supports multilingual document search: English, Japanese, Vietnamese
"""

from __future__ import annotations
import math
import logging
import logging.handlers
import os
import re
import platform
import signal
import subprocess
import threading
import json
from pathlib import Path
from urllib.parse import urlencode, urlparse, parse_qs, urlunparse, quote
from flask import Flask, render_template, request, jsonify, Response
from flask_socketio import SocketIO
from werkzeug.middleware.proxy_fix import ProxyFix
import pysolr
import config as cfg
from werkzeug.middleware.dispatcher import DispatcherMiddleware
from indexing.indexing_service import IndexingStateStore, IndexingWorker, IndexJobConfig
from indexing.semantic_service import SemanticSearchService, SQL_HINT_TERMS
from providers.proxy_chat import ProxyChatClient, ProxyChatError

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# ── Subdirectory & Proxy Handling ──────────────────────────────────────────────
# 1. Standard Proxy Handling (Works for IIS/Nginx etc.)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

# 2. Local Subdirectory Simulation
# This is only for local development to simulate how it behaves in a sub-folder.
# In Production (IIS), set SIMULATE_SUBDIRECTORY = False.
if cfg.APP_PREFIX and getattr(cfg, "SIMULATE_SUBDIRECTORY", False):

    prefix = "/" + cfg.APP_PREFIX.strip("/")

    def root_app(environ, start_response):
        # If we are behind a proxy, don't perform the simulation redirect
        # to avoid "Too many redirects" loop.
        if "HTTP_X_FORWARDED_PREFIX" in environ or "HTTP_X_FORWARDED_FOR" in environ:
            return app.wsgi_app(environ, start_response)

        path = environ.get("PATH_INFO", "")
        if path == "/" or not path:
            url = prefix + "/"
            res = Response(
                f"Redirecting to {url}...", status=302, headers=[("Location", url)]
            )
            return res(environ, start_response)

        res = Response("Not Found", status=404)
        return res(environ, start_response)

    app.wsgi_app = DispatcherMiddleware(root_app, {prefix: app.wsgi_app})

app.logger.setLevel(logging.INFO)

# ── Indexing logger (console + rotating file) ──────────────────────────────────
_log_dir = Path(__file__).parent / "logs"
_log_dir.mkdir(exist_ok=True)

_log_formatter = logging.Formatter(
    "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

_file_handler = logging.handlers.RotatingFileHandler(
    _log_dir / "indexing.log",
    maxBytes=5 * 1024 * 1024,  # 5 MB
    backupCount=3,
    encoding="utf-8",
)
_file_handler.setFormatter(_log_formatter)
_file_handler.setLevel(logging.DEBUG)

_console_handler = logging.StreamHandler()
_console_handler.setFormatter(_log_formatter)
_console_handler.setLevel(logging.INFO)

_indexing_logger = logging.getLogger("indexing")
_indexing_logger.setLevel(logging.DEBUG)
_indexing_logger.addHandler(_file_handler)
_indexing_logger.addHandler(_console_handler)
_indexing_logger.propagate = False

# Also route Flask app.logger to the same file so chat/API errors are captured
_chat_file_handler = logging.handlers.RotatingFileHandler(
    _log_dir / "chat.log",
    maxBytes=5 * 1024 * 1024,
    backupCount=3,
    encoding="utf-8",
)
_chat_file_handler.setFormatter(_log_formatter)
_chat_file_handler.setLevel(logging.DEBUG)
app.logger.setLevel(logging.DEBUG)
app.logger.addHandler(_chat_file_handler)
app.logger.addHandler(_console_handler)


_shutdown_lock = threading.Lock()
_shutdown_signal_count = 0


def _force_exit_later(delay_seconds: int) -> None:
    def _runner():
        try:
            threading.Event().wait(max(1, delay_seconds))
        finally:
            os._exit(130)

    t = threading.Thread(target=_runner, daemon=True, name="force-exit")
    t.start()


def _handle_shutdown_signal(signum, frame) -> None:
    global _shutdown_signal_count
    with _shutdown_lock:
        _shutdown_signal_count += 1
        count = _shutdown_signal_count

    if count >= 2:
        app.logger.warning("Received repeated shutdown signal; forcing exit now.")
        os._exit(130)

    app.logger.info("Shutdown signal received (%s). Stopping worker...", signum)
    try:
        active = indexing_store.get_active_job()
        if active:
            indexing_store.request_cancel(int(active["id"]))
    except Exception:
        pass

    try:
        indexing_worker.stop()
    except Exception:
        pass

    timeout_seconds = int(getattr(cfg, "SHUTDOWN_FORCE_EXIT_SECONDS", 8) or 8)
    app.logger.info(
        "Graceful shutdown started; process will be force-killed in %ds if still alive.",
        timeout_seconds,
    )
    _force_exit_later(timeout_seconds)


# ── Jinja2 custom filters ──────────────────────────────────────────────────────


@app.template_filter("str_val")
def str_val_filter(value) -> str:
    """Safely coerce a Solr field to str — extracts first element if it's a list."""
    if isinstance(value, list):
        return value[0] if value else ""
    return value or ""


@app.template_filter("basename")
def basename_filter(value) -> str:
    """Return the final component of a file path, handling both / and \\ separators."""
    if isinstance(value, list):
        value = value[0] if value else ""
    if not value:
        return value
    from posixpath import basename as posix_basename
    from ntpath import basename as nt_basename

    # If path contains backslashes, ntpath gives the real filename
    return nt_basename(value) if "\\" in value else posix_basename(value)


@app.template_filter("format_number")
def fmt_number(value):
    """Format an integer with thousands separators."""
    try:
        return f"{int(value):,}"
    except (ValueError, TypeError):
        return value


@app.template_filter("remove_param")
def remove_param(url: str, param: str) -> str:
    """Return the URL with the specified query param removed."""
    parsed = urlparse(url)
    qs = parse_qs(parsed.query, keep_blank_values=True)
    qs.pop(param, None)
    new_query = urlencode(qs, doseq=True)
    return urlunparse(parsed._replace(query=new_query))


@app.template_filter("set_param")
def set_param(url: str, param: str, value: str) -> str:
    """Return the URL with the specified query param set to a new value."""
    parsed = urlparse(url)
    qs = parse_qs(parsed.query, keep_blank_values=True)
    qs[param] = [value]
    new_query = urlencode(qs, doseq=True)
    return urlunparse(parsed._replace(query=new_query))


@app.template_filter("add_facet")
def add_facet(url: str, field: str, value: str) -> str:
    """Add or update a facet parameter and reset page to 1."""
    parsed = urlparse(url)
    qs = parse_qs(parsed.query, keep_blank_values=True)
    qs[field] = [value]
    qs["page"] = ["1"]
    new_query = urlencode(qs, doseq=True)
    return urlunparse(parsed._replace(query=new_query))


def facet_param_name(field: str) -> str:
    """Map Solr facet field names to request query parameter names."""
    facet_param_map = {
        cfg.FIELD_REPOSITORY: "repository",
        cfg.FIELD_FILE_TYPE: "file_type",
    }
    return facet_param_map.get(field, field)


@app.template_filter("toggle_facet")
def toggle_facet(url: str, field: str, value: str) -> str:
    """Toggle a facet value on/off (multi-select) and reset page to 1."""
    parsed = urlparse(url)
    qs = parse_qs(parsed.query, keep_blank_values=True)
    param = facet_param_name(field)

    current = qs.get(param, [])
    if value in current:
        current = [v for v in current if v != value]
    else:
        current = current + [value]

    if current:
        qs[param] = current
    else:
        qs.pop(param, None)

    qs["page"] = ["1"]
    new_query = urlencode(qs, doseq=True)
    return urlunparse(parsed._replace(query=new_query))


@app.template_global("local_file_href")
def local_file_href(doc: dict) -> str | None:
    """Build a local file:// link for a search result when the file exists."""
    file_path = doc.get(cfg.FIELD_FILE_PATH) or doc.get("file_path")
    if not file_path:
        return None

    path = Path(file_path)
    if not path.is_absolute():
        local_root = Path(
            getattr(cfg, "LOCAL_FILE_ROOT", Path(__file__).resolve().parent)
        )
        path = local_root / file_path

    if not path.exists():
        return None

    return path.resolve().as_uri()


@app.template_global("repository_href")
def repository_href(doc: dict) -> str | None:
    """Build a repository browser URL for a search result.

    Priority:
    1. doc['url']             — direct full URL stored per-document
    2. doc['repository_path'] — base URL stored per-document + file_path
    """
    direct_url = doc.get(cfg.FIELD_URL) or doc.get("url")
    if direct_url:
        return direct_url

    file_path = doc.get(cfg.FIELD_FILE_PATH) or doc.get("file_path")

    # Per-document base URL (preferred: stored in Solr)
    repo_path = doc.get(cfg.FIELD_REPOSITORY_PATH) or doc.get("repository_path")
    if repo_path and file_path:
        # file_path here is an absolute OS path — only safe to append when it is
        # a relative path (e.g. legacy docs without a direct 'url' field).
        fp = Path(file_path)
        if fp.is_absolute():
            # Cannot safely build a URL from an absolute OS path; return base only.
            return repo_path
        rel = fp.as_posix()  # forward-slash on all platforms
        return f"{repo_path.rstrip('/')}/{quote(rel, safe='/:@')}"
    if repo_path:
        return repo_path


# ── Solr client ────────────────────────────────────────────────────────────────
solr = pysolr.Solr(cfg.SOLR_URL, timeout=cfg.SOLR_TIMEOUT, always_commit=False)
indexing_store = IndexingStateStore(
    Path(__file__).resolve().parent / "data/indexing.db"
)
try:
    semantic_service = SemanticSearchService(
        cfg.QDRANT_STORAGE_PATH,
        model_name=cfg.SEMANTIC_MODEL,
        collection_name=cfg.QDRANT_COLLECTION,
    )
except Exception as exc:
    app.logger.warning("Semantic service disabled: %s", exc)
    semantic_service = None
indexing_worker = IndexingWorker(
    indexing_store,
    solr,
    semantic_service=semantic_service,
    on_change=lambda: _notify_clients(),
)
default_index_sources_file = (
    Path(__file__).resolve().parent / "indexing" / "index_sources.json"
)
user_index_sources_file = (
    Path(__file__).resolve().parent / "data/index_sources.user.json"
)


@app.before_request
def ensure_indexing_worker() -> None:
    """Start the indexing worker lazily for this Flask process."""
    if not indexing_worker.is_running:
        indexing_worker.start()


def _normalise_extensions(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip().lstrip(".") for v in value if str(v).strip()]
    if isinstance(value, str):
        return [v.strip().lstrip(".") for v in value.split(",") if v.strip()]
    return []


def _normalise_source(src: dict, default_flag: bool = False) -> dict:
    # Normalise root_path to forward slashes so the value is consistent
    # whether the JSON was written on Windows (backslashes) or macOS/Linux.
    root_path = str(src.get("root_path", "")).strip().replace("\\", "/")
    return {
        "name": str(src.get("name", "")).strip(),
        "root_path": root_path,
        "repository": str(src.get("repository", "")).strip(),
        "repository_path": str(src.get("repository_path", ""))
        .strip()
        .replace("\\", "/"),
        "repository_url_base": str(src.get("repository_url_base", "")).strip(),
        "extensions": _normalise_extensions(src.get("extensions", [])),
        "is_default": bool(src.get("is_default", default_flag)),
    }


def _load_sources_file(path: Path, default_flag: bool = False) -> list[dict]:
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            return []
        out = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            item_norm = _normalise_source(item, default_flag=default_flag)
            if not item_norm["root_path"]:
                continue
            out.append(item_norm)
        return out
    except Exception as exc:
        app.logger.error("Failed loading source config from %s: %s", path, exc)
        return []


def _save_user_sources(items: list[dict]) -> None:
    user_index_sources_file.parent.mkdir(parents=True, exist_ok=True)
    payload = [
        {
            "name": i["name"],
            "root_path": i["root_path"],
            "repository": i["repository"],
            "repository_path": i["repository_path"],
            "repository_url_base": i["repository_url_base"],
            "extensions": i["extensions"],
        }
        for i in items
    ]
    user_index_sources_file.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )


def get_all_index_sources() -> list[dict]:
    default_items = _load_sources_file(default_index_sources_file, default_flag=True)
    user_items = _load_sources_file(user_index_sources_file, default_flag=False)
    return default_items + user_items


def build_qf_string() -> str:
    """Build edismax qf string from configured query fields with boosts."""
    return " ".join(f"{field}^{boost}" for field, boost in cfg.QUERY_FIELDS.items())


def parse_form(args: dict) -> dict:
    """Extract and sanitise search parameters from the request args/form."""
    query = args.get("q", "").strip()

    # --- Query modifiers ---
    phrase_mode = args.get("phrase", "") == "1"
    exact_terms = args.get("exact", "") == "1"
    fuzzy = args.get("fuzzy", "0").strip()
    operator = args.get("op", cfg.DEFAULT_OPERATOR)
    parser = args.get("parser", cfg.DEFAULT_PARSER)
    search_mode = args.get("mode", "fulltext").strip().lower()
    if search_mode not in {"fulltext", "semantic", "hybrid"}:
        search_mode = "fulltext"
    boost_func = args.get("bf", "").strip()
    debug_mode = args.get("debug", "") == "1"

    # --- Filters ---
    repository = [v.strip() for v in args.getlist("repository") if v.strip()]
    file_type = [v.strip() for v in args.getlist("file_type") if v.strip()]
    date_from = args.get("date_from", "").strip()
    date_to = args.get("date_to", "").strip()
    extra_fq = args.get("fq", "").strip()  # raw fq from user

    # --- Display ---
    sort_val = args.get("sort", "score desc")
    rows = min(int(args.get("rows", cfg.DEFAULT_ROWS)), 100)
    page = max(int(args.get("page", 1)), 1)
    start = (page - 1) * rows

    # --- Proximity ---
    proximity = args.get("proximity", "0").strip()

    # --- Minimum Should Match ---
    mm_val = args.get("mm", "").strip()  # e.g. "75%" or "3" or empty = auto

    # --- Group by ---
    group_by = args.get(
        "group_by", ""
    ).strip()  # field name, e.g. file_path, repository

    hl_enabled = args.get("hl", "1") != "0"

    return dict(
        query=query,
        phrase_mode=phrase_mode,
        exact_terms=exact_terms,
        fuzzy=fuzzy,
        proximity=proximity,
        operator=operator,
        parser=parser,
        search_mode=search_mode,
        boost_func=boost_func,
        debug_mode=debug_mode,
        repository=repository,
        file_type=file_type,
        date_from=date_from,
        date_to=date_to,
        extra_fq=extra_fq,
        sort_val=sort_val,
        rows=rows,
        page=page,
        start=start,
        mm_val=mm_val,
        group_by=group_by,
        hl_enabled=hl_enabled,
    )


def build_solr_params(p: dict) -> tuple[str, dict]:
    """Build the Solr query string and params dict from parsed form params."""
    q = p["query"] or "*:*"

    # Proximity search: "term1 term2"~N  (takes priority over fuzzy/phrase)
    try:
        prox = int(p["proximity"])
    except (ValueError, TypeError):
        prox = 0

    if prox > 0 and q != "*:*":
        # Strip any existing quotes, then wrap as proximity phrase
        q_clean = q.strip('"')
        q = f'"{q_clean}"~{prox}'
    else:
        # Exact terms (quote each word independently)
        if q != "*:*" and p["exact_terms"] and not p["phrase_mode"]:
            # Wrap each term in quotes to bypass splitting/fuzzying
            q = " ".join(
                f'"{term}"' if not term.startswith("-") else term for term in q.split()
            )

        # Apply fuzzy to each individual term (only when not phrase/exact/proximity mode)
        elif q != "*:*" and not p["phrase_mode"]:
            try:
                fuzz = int(p["fuzzy"])
                if fuzz > 0:
                    q = " ".join(
                        f"{term}~{fuzz}" if not term.startswith("-") else term
                        for term in q.split()
                    )
            except ValueError:
                pass

        # Wrap in quotes for exact phrase search
        if p["phrase_mode"] and q != "*:*":
            q = f'"{q}"'

    fqs: list[str] = []
    if p["repository"]:
        if len(p["repository"]) == 1:
            fqs.append(
                f'{{!tag=repository}}{cfg.FIELD_REPOSITORY}:"{p["repository"][0]}"'
            )
        else:
            inner = " OR ".join(f'"{v}"' for v in p["repository"])
            fqs.append(f"{{!tag=repository}}{cfg.FIELD_REPOSITORY}:({inner})")
    if p["file_type"]:
        if len(p["file_type"]) == 1:
            fqs.append(f'{{!tag=file_type}}{cfg.FIELD_FILE_TYPE}:"{p["file_type"][0]}"')
        else:
            inner = " OR ".join(f'"{v}"' for v in p["file_type"])
            fqs.append(f"{{!tag=file_type}}{cfg.FIELD_FILE_TYPE}:({inner})")

    # Date range
    df = p["date_from"] or "*"
    dt = p["date_to"] or "*"
    if p["date_from"] or p["date_to"]:
        fqs.append(f"{cfg.FIELD_DATE}:[{df}T00:00:00Z TO {dt}T23:59:59Z]")

    if p["extra_fq"]:
        fqs.append(p["extra_fq"])

    # Facet fields with exclusion local params so multi-select stays visible
    facet_fields_ex = [f"{{!ex={f}}}{f}" for f in cfg.FACET_FIELDS]

    params: dict = {
        "defType": p["parser"],
        "q.op": p["operator"],
        "sort": p["sort_val"],
        "rows": p["rows"],
        "start": p["start"],
        # Facets
        "facet": "true",
        "facet.field": facet_fields_ex,
        "facet.mincount": 1,
        "facet.limit": 20,
        # Highlighting
        "hl": "true" if p["hl_enabled"] else "false",
        "hl.fl": cfg.HL_FIELDS,
        "hl.snippets": cfg.HL_SNIPPETS,
        "hl.fragsize": cfg.HL_FRAG_SIZE,
        "hl.simple.pre": cfg.HL_PRE_TAG,
        "hl.simple.post": cfg.HL_POST_TAG,
        "hl.requireFieldMatch": "false",
        "hl.encoder": "html",
    }

    # edismax specific
    if p["parser"] == "edismax":
        params["qf"] = build_qf_string()
        # mm: use user value if set, else default to "1" (at least 1 term must match)
        params["mm"] = p["mm_val"] if p["mm_val"] else "1"

    # Group by file / repository
    if p["group_by"]:
        params["group"] = "true"
        params["group.field"] = p["group_by"]
        params["group.limit"] = 10  # top 10 docs per group
        params["group.ngroups"] = "true"  # count distinct groups
        # When grouping, sort within each group by score
        params["group.sort"] = "score desc"

    if p["boost_func"]:
        params["bf"] = p["boost_func"]

    if fqs:
        params["fq"] = fqs

    if p["debug_mode"]:
        params["debugQuery"] = "true"

    return q, params


def execute_search(p: dict) -> dict:
    """Run the Solr query and return a normalised result dict."""
    mode = p.get("search_mode", "fulltext")
    if mode == "semantic":
        return execute_semantic_search(p)
    if mode == "hybrid":
        return execute_hybrid_search(p)

    return execute_fulltext_search(p)


def execute_fulltext_search(p: dict) -> dict:
    """Run full-text Solr search only."""
    q, params = build_solr_params(p)

    try:
        results = solr.search(q, **params)
    except pysolr.SolrError as exc:
        app.logger.error("Solr error: %s", exc)
        return {
            "error": str(exc),
            "docs": [],
            "groups": [],
            "num_found": 0,
            "facets": {},
            "highlighting": {},
            "debug": {},
            "grouped": False,
        }

    # Parse facets
    facets: dict[str, list] = {}
    raw_ff = getattr(results, "facets", {}).get("facet_fields", {})
    for field, items in raw_ff.items():
        pairs = []
        it = iter(items)
        for val in it:
            cnt = next(it, 0)
            pairs.append((val, cnt))
        facets[field] = pairs

    # ── Handle grouped response ───────────────────────────────────────────────
    grouped_response = getattr(results, "raw_response", {}).get("grouped", {})
    is_grouped = bool(p.get("group_by") and grouped_response)

    groups = []
    num_found = 0
    if is_grouped:
        group_field_data = grouped_response.get(p["group_by"], {})
        num_found = group_field_data.get("ngroups", 0) or group_field_data.get(
            "matches", 0
        )
        for grp in group_field_data.get("groups", []):
            groups.append(
                {
                    "key": grp.get("groupValue", "(unknown)"),
                    "count": grp.get("doclist", {}).get("numFound", 0),
                    "docs": grp.get("doclist", {}).get("docs", []),
                }
            )
        total_pages = math.ceil(len(groups) / p["rows"]) if p["rows"] else 1
        docs = []
    else:
        num_found = results.hits
        total_pages = math.ceil(num_found / p["rows"]) if p["rows"] else 1
        docs = list(results)

    return {
        "docs": docs,
        "groups": groups,
        "grouped": is_grouped,
        "num_found": num_found,
        "total_pages": total_pages,
        "page": p["page"],
        "rows": p["rows"],
        "start": p["start"],
        "facets": facets,
        "highlighting": (
            results.highlighting if hasattr(results, "highlighting") else {}
        ),
        "debug": results.debug if hasattr(results, "debug") else {},
        "error": None,
        "mode": "fulltext",
        "q": q,
        "params": params,
    }


def _semantic_to_docs(items: list[dict]) -> list[dict]:
    docs = []
    for idx, item in enumerate(items):
        fp = str(item.get("file_path", ""))
        rel = str(item.get("rel_path", "")).replace("\\", "/")
        title = Path(fp).name if fp else f"semantic-{idx}"
        repo_url_base = item.get("repository_url_base", "")

        # Build direct URL (same logic as index time: base/rel_path)
        url = ""
        if repo_url_base and rel:
            url = f"{repo_url_base.rstrip('/')}/{rel}"

        docs.append(
            {
                "id": f"semantic-{idx}-{item.get('chunk_id', '')}",
                "title": title,
                "file_path": fp,
                "repository": item.get("repository", ""),
                "repository_path": repo_url_base,
                "file_type": item.get("file_type", ""),
                "content": item.get("text", ""),
                "semantic_score": item.get("score", 0.0),
                "url": url,
            }
        )
    return docs


def _semantic_sql_filter(query: str, docs: list[dict]) -> list[dict]:
    """Reduce semantic noise for SQL-like queries by lexical sanity checks."""
    terms = [t.lower() for t in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", query)]
    sql_terms = [t for t in terms if t in SQL_HINT_TERMS]
    identifiers = [t.lower() for t in re.findall(r"\b[A-Z_][A-Z0-9_]{2,}\b", query)]

    if not sql_terms:
        return docs

    filtered: list[dict] = []
    for doc in docs:
        haystack = "\n".join(
            [
                str(doc.get("title", "")).lower(),
                str(doc.get("file_path", "")).lower(),
                str(doc.get("content", "")).lower(),
            ]
        )
        has_sql = any(t in haystack for t in sql_terms)
        has_ident = (not identifiers) or any(t in haystack for t in identifiers)
        if has_sql and has_ident:
            filtered.append(doc)

    # If query has explicit identifier (e.g., CUSTOMER), keep strict filtering
    # even when no match is found to avoid noisy false positives.
    if identifiers:
        return filtered

    return filtered if filtered else docs


def _is_sql_like_query(query: str) -> bool:
    terms = [t.lower() for t in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", query)]
    return any(t in SQL_HINT_TERMS for t in terms)


def _compute_facets(docs: list[dict]) -> dict:
    """Compute facet counts from a list of result docs."""
    from collections import Counter

    repo_counts = Counter(d["repository"] for d in docs if d.get("repository"))
    ftype_counts = Counter(d["file_type"] for d in docs if d.get("file_type"))
    facets: dict = {}
    if repo_counts:
        facets[cfg.FIELD_REPOSITORY] = sorted(repo_counts.items(), key=lambda x: -x[1])
    if ftype_counts:
        facets[cfg.FIELD_FILE_TYPE] = sorted(ftype_counts.items(), key=lambda x: -x[1])
    return facets


def _apply_grouping(docs: list[dict], p: dict) -> tuple[list, bool, list]:
    """Return (groups, is_grouped, paged_docs). Groups if p['group_by'] is set."""
    group_by = p.get("group_by", "")
    if not group_by:
        start = p["start"]
        return [], False, docs[start : start + p["rows"]]

    groups_map: dict[str, dict] = {}
    for doc in docs:
        key = str(doc.get(group_by) or "(unknown)")
        if key not in groups_map:
            groups_map[key] = {"key": key, "count": 0, "docs": []}
        groups_map[key]["count"] += 1
        if len(groups_map[key]["docs"]) < 10:
            groups_map[key]["docs"].append(doc)

    all_groups = sorted(groups_map.values(), key=lambda g: -g["count"])
    start = p["start"]
    paged_groups = all_groups[start : start + p["rows"]]
    return paged_groups, True, []


def execute_semantic_search(p: dict) -> dict:
    """Run semantic vector search over indexed chunks."""
    if semantic_service is None:
        return {
            "docs": [],
            "groups": [],
            "grouped": False,
            "num_found": 0,
            "total_pages": 1,
            "page": p["page"],
            "rows": p["rows"],
            "start": p["start"],
            "facets": {},
            "highlighting": {},
            "debug": {},
            "error": "Semantic search unavailable: install qdrant and embedding dependencies.",
            "mode": "semantic",
            "q": p["query"],
            "params": {"mode": "semantic"},
        }

    # Fetch enough results to support the requested page + facet computation
    fetch_rows = max(p["rows"] * p["page"], p["rows"], 100)
    items = semantic_service.search(
        query=p["query"],
        rows=fetch_rows,
        repositories=p["repository"],
        file_types=p["file_type"],
        min_score=cfg.SEMANTIC_MIN_SCORE,
    )
    all_docs = _semantic_to_docs(items)
    all_docs = _semantic_sql_filter(p["query"], all_docs)

    if not all_docs and _is_sql_like_query(p["query"]):
        fallback = execute_fulltext_search(p)
        fallback["mode"] = "semantic"
        fallback["params"] = {**p, "mode": "semantic", "fallback": "fulltext_sql"}
        fallback["debug"] = {
            **(fallback.get("debug") or {}),
            "semantic_fallback": "fulltext_sql",
        }
        return fallback

    # Client-side facets from result set
    facets = _compute_facets(all_docs)

    # Client-side group-by
    groups, is_grouped, paged_docs = _apply_grouping(all_docs, p)

    num_found = len(all_docs)
    total_pages = math.ceil(num_found / p["rows"]) if p["rows"] else 1

    return {
        "docs": paged_docs,
        "groups": groups,
        "grouped": is_grouped,
        "num_found": num_found,
        "total_pages": total_pages,
        "page": p["page"],
        "rows": p["rows"],
        "start": p["start"],
        "facets": facets,
        "highlighting": {},
        "debug": {},
        "error": None,
        "mode": "semantic",
        "q": p["query"],
        "params": {**p, "mode": "semantic"},
    }


def execute_hybrid_search(p: dict) -> dict:
    """Combine full-text and semantic results using reciprocal rank fusion."""
    if semantic_service is None:
        return execute_fulltext_search(p)

    p_full = dict(p)
    p_full["group_by"] = ""
    full = execute_fulltext_search(p_full)
    if full.get("error"):
        return full

    sem_items = semantic_service.search(
        query=p["query"],
        rows=max(p["rows"] * 3, 30),
        repositories=p["repository"],
        file_types=p["file_type"],
        min_score=cfg.SEMANTIC_MIN_SCORE,
    )
    sem_docs = _semantic_to_docs(sem_items)

    def key_of(doc: dict) -> str:
        return str(doc.get("file_path") or doc.get("id") or "")

    merged: dict[str, dict] = {}
    k = 60.0

    for rank, doc in enumerate(full.get("docs", []), start=1):
        key = key_of(doc)
        if not key:
            continue
        base = dict(doc)
        base["hybrid_score"] = 1.0 / (k + rank)
        base["fulltext_rank"] = rank
        merged[key] = base

    for rank, doc in enumerate(sem_docs, start=1):
        key = key_of(doc)
        if not key:
            continue
        score = 1.0 / (k + rank)
        if key in merged:
            merged[key]["hybrid_score"] += score
            merged[key]["semantic_score"] = doc.get("semantic_score", 0.0)
            if not merged[key].get("content"):
                merged[key]["content"] = doc.get("content", "")
        else:
            base = dict(doc)
            base["hybrid_score"] = score
            merged[key] = base

    ordered = sorted(
        merged.values(), key=lambda x: float(x.get("hybrid_score", 0.0)), reverse=True
    )

    # Client-side facets and grouping on merged set
    facets = _compute_facets(ordered)
    groups, is_grouped, paged = _apply_grouping(ordered, p)
    if not is_grouped:
        start = p["start"]
        paged = ordered[start : start + p["rows"]]

    num_found = len(ordered)
    total_pages = math.ceil(num_found / p["rows"]) if p["rows"] else 1

    return {
        "docs": paged,
        "groups": groups,
        "grouped": is_grouped,
        "num_found": num_found,
        "total_pages": total_pages,
        "page": p["page"],
        "rows": p["rows"],
        "start": p["start"],
        "facets": facets,
        "highlighting": full.get("highlighting", {}),
        "debug": full.get("debug", {}),
        "error": None,
        "mode": "hybrid",
        "q": p["query"],
        "params": {**p, "mode": "hybrid"},
    }


# ── Routes ─────────────────────────────────────────────────────────────────────


@app.route("/")
def index():
    p = parse_form(request.args)
    # Only execute search when the user has submitted a query or filter
    has_input = bool(
        p["query"]
        or p["repository"]
        or p["file_type"]
        or p["date_from"]
        or p["date_to"]
        or p["extra_fq"]
    )
    data = execute_search(p) if has_input else None
    return render_template(
        "index.html",
        file_types=cfg.FILE_TYPES,
        sort_options=cfg.SORT_OPTIONS,
        default_rows=cfg.DEFAULT_ROWS,
        search=data,
        params=p,
    )


@app.route("/search")
def search():
    p = parse_form(request.args)
    if not p["query"]:
        from flask import redirect, url_for

        return redirect(url_for("index"))
    data = execute_search(p)
    return render_template(
        "index.html",
        file_types=cfg.FILE_TYPES,
        sort_options=cfg.SORT_OPTIONS,
        default_rows=cfg.DEFAULT_ROWS,
        search=data,
        params=p,
    )


@app.route("/api/search")
def api_search():
    """JSON API — called by the frontend via fetch."""
    p = parse_form(request.args)
    data = execute_search(p)
    # Make docs JSON-serialisable (values may be lists)
    data["docs"] = [
        {
            k: (v[0] if isinstance(v, list) and len(v) == 1 else v)
            for k, v in doc.items()
        }
        for doc in data["docs"]
    ]
    return jsonify(data)


@app.route("/api/open-file")
def api_open_file():
    """Open a local file/folder in the OS file manager (Explorer / Finder).
    Only resolves paths under LOCAL_FILE_ROOT for safety.
    """
    file_path = request.args.get("path", "").strip()
    if not file_path:
        return jsonify({"error": "No path provided"}), 400

    local_root = Path(getattr(cfg, "LOCAL_FILE_ROOT", Path(__file__).resolve().parent))
    path = Path(file_path)
    if not path.is_absolute():
        path = local_root / file_path

    # Security: ensure resolved path is under LOCAL_FILE_ROOT
    try:
        path = path.resolve()
        path.relative_to(local_root.resolve())
    except ValueError:
        return jsonify({"error": "Path outside allowed root"}), 403

    if not path.exists():
        return jsonify({"error": "File not found"}), 404

    system = platform.system()
    try:
        if system == "Windows":
            # Open Explorer with the file selected
            subprocess.Popen(["explorer", "/select,", str(path)])
        elif system == "Darwin":
            # Open Finder with the file revealed
            subprocess.Popen(["open", "-R", str(path)])
        else:
            # Linux: open parent folder
            subprocess.Popen(["xdg-open", str(path.parent)])
    except Exception as exc:
        app.logger.error("open-file error: %s", exc)
        return jsonify({"error": str(exc)}), 500

    return jsonify({"ok": True, "path": str(path)})


@app.route("/indexing")
def indexing_page():
    prefix = "/" + cfg.APP_PREFIX.strip("/") if cfg.APP_PREFIX else ""
    socket_io_path = prefix + "/socket.io"
    return render_template("indexing.html", socket_io_path=socket_io_path)


# ── Run-All state (thread-safe) ────────────────────────────────────────────────
_run_all_lock = threading.Lock()
_run_all_state: dict = {
    "running": False,
    "total": 0,
    "done": 0,
    "log": [],
}


def _vcs_update(vcs_path: Path, name: str, log_fn) -> None:
    """Run git pull or svn update on vcs_path. Logs results via log_fn."""
    if (vcs_path / ".git").exists():
        log_fn(f"[{name}] git pull {vcs_path}")
        try:
            result = subprocess.run(
                ["git", "-C", str(vcs_path), "pull"],
                capture_output=True,
                text=True,
                timeout=180,
            )
            msg = result.stdout.strip() or result.stderr.strip() or "(no output)"
            status = "OK" if result.returncode == 0 else "FAILED"
            for line in msg.splitlines():
                log_fn(f"[{name}] git {status}: {line}")
        except subprocess.TimeoutExpired:
            log_fn(f"[{name}] git pull timed out")
        except FileNotFoundError:
            log_fn(f"[{name}] git not found in PATH")
    elif (vcs_path / ".svn").exists():
        log_fn(f"[{name}] svn update {vcs_path}")
        try:
            result = subprocess.run(
                ["svn", "update", str(vcs_path)],
                capture_output=True,
                text=True,
                timeout=180,
            )
            msg = result.stdout.strip() or result.stderr.strip() or "(no output)"
            status = "OK" if result.returncode == 0 else "FAILED"
            for line in msg.splitlines():
                log_fn(f"[{name}] svn {status}: {line}")
        except subprocess.TimeoutExpired:
            log_fn(f"[{name}] svn update timed out")
        except FileNotFoundError:
            log_fn(f"[{name}] svn not found in PATH")
    else:
        log_fn(f"[{name}] No VCS detected, skipping update")


def _run_all_worker() -> None:
    sources = get_all_index_sources()

    def _log(msg: str) -> None:
        _indexing_logger.info("run-all: %s", msg)
        with _run_all_lock:
            _run_all_state["log"].append(msg)
            _run_all_state["log"] = _run_all_state["log"][-100:]
        _notify_clients()

    with _run_all_lock:
        _run_all_state.update(
            {"running": True, "total": len(sources), "done": 0, "log": []}
        )

    _log(f"Starting run-all for {len(sources)} sources")

    for src in sources:
        root = Path(src["root_path"])
        repo_sub = src.get("repository_path", "").strip()
        vcs_path = root / repo_sub if repo_sub else root
        name = src.get("name") or src.get("repository") or str(root)

        try:
            _vcs_update(vcs_path, name, _log)
        except Exception as exc:
            _log(f"[{name}] VCS update error: {exc}")

        try:
            existing = indexing_store.get_pending_job_for_source(
                src["root_path"], src.get("repository_path", "")
            )
            if existing:
                _log(
                    f"[{name}] Already queued as job #{existing['id']} ({existing['status']}), skipping"
                )
            else:
                src_mode = src.get("index_mode", "both")
                if src_mode not in ("both", "solr", "semantic"):
                    src_mode = "both"
                job_id = indexing_store.create_job(
                    IndexJobConfig(
                        root_path=src["root_path"],
                        repository=src.get("repository", ""),
                        repository_path=src.get("repository_path", ""),
                        repository_url_base=src.get("repository_url_base", ""),
                        extensions=src.get("extensions", []),
                        index_mode=src_mode,
                    )
                )
                _log(f"[{name}] Queued as job #{job_id}")
        except Exception as exc:
            _log(f"[{name}] Failed to queue: {exc}")

        with _run_all_lock:
            _run_all_state["done"] += 1

    indexing_worker.start()
    _log("All sources queued — worker started")

    with _run_all_lock:
        _run_all_state["running"] = False
    _notify_clients()


@socketio.on("connect")
def _on_socket_connect():
    socketio.emit("indexing_status", _build_status_payload())


def _build_status_payload() -> dict:
    active = indexing_store.get_active_job()
    with _run_all_lock:
        run_all = dict(_run_all_state)
    return {
        "ok": True,
        "worker_running": indexing_worker.is_running,
        "active_job": dict(active) if active else None,
        "recent_jobs": indexing_store.list_recent_jobs(limit=12),
        "recent_files": indexing_store.list_recent_indexed_files(limit=40),
        "run_all": run_all,
    }


def _notify_clients() -> None:
    try:
        socketio.emit("indexing_status", _build_status_payload(), namespace="/")
    except Exception:
        pass


@app.route("/api/indexing/sources")
def api_indexing_sources():
    return jsonify({"ok": True, "sources": get_all_index_sources()})


@app.route("/api/indexing/sources", methods=["POST"])
def api_indexing_sources_add():
    body = request.get_json(silent=True) or {}
    source = _normalise_source(body)

    # Repository Name drives both the scan sub-folder and the label
    repo_name = source["name"]
    if not repo_name:
        return (
            jsonify({"ok": False, "error": "name (Repository Name) is required"}),
            400,
        )
    if not source["root_path"]:
        return jsonify({"ok": False, "error": "root_path is required"}), 400

    root = Path(source["root_path"]).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        return (
            jsonify({"ok": False, "error": "root_path must be an existing folder"}),
            400,
        )

    repo_dir = root / repo_name
    if not repo_dir.exists() or not repo_dir.is_dir():
        return (
            jsonify(
                {
                    "ok": False,
                    "error": f"Repository folder '{repo_name}' not found inside root_path",
                }
            ),
            400,
        )

    source["root_path"] = root.as_posix()
    source["repository"] = repo_name
    source["repository_path"] = repo_name
    current_user_sources = _load_sources_file(
        user_index_sources_file, default_flag=False
    )

    for item in current_user_sources:
        if (
            item["root_path"] == source["root_path"]
            and item["repository_path"] == source["repository_path"]
        ):
            return jsonify({"ok": False, "error": "source already exists"}), 409

    current_user_sources.append(source)
    _save_user_sources(current_user_sources)
    return jsonify({"ok": True, "source": source})


@app.route("/api/indexing/start", methods=["POST"])
def api_indexing_start():
    body = request.get_json(silent=True) or {}

    root_path = str(body.get("root_path", "")).strip()
    # Repository Name = sub-folder name inside root_path
    repo_name = str(body.get("name", "")).strip()
    repository_url_base = str(body.get("repository_url_base", "")).strip()
    ext_raw = str(body.get("extensions", "")).strip()
    index_mode = str(body.get("index_mode", "both")).strip()
    if index_mode not in ("both", "solr", "semantic"):
        index_mode = "both"

    if not root_path:
        return jsonify({"ok": False, "error": "root_path is required"}), 400
    if not repo_name:
        return (
            jsonify({"ok": False, "error": "name (Repository Name) is required"}),
            400,
        )

    root = Path(root_path).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        return (
            jsonify({"ok": False, "error": "root_path must be an existing folder"}),
            400,
        )

    repo_dir = root / repo_name
    if not repo_dir.exists() or not repo_dir.is_dir():
        return (
            jsonify(
                {
                    "ok": False,
                    "error": f"Repository folder '{repo_name}' not found inside root_path",
                }
            ),
            400,
        )

    # repository_path = repo_name (scan root_path/repo_name)
    repository_path = repo_name
    repository = repo_name

    existing = indexing_store.get_pending_job_for_source(
        root.as_posix(), repository_path
    )
    if existing:
        return (
            jsonify(
                {
                    "ok": False,
                    "error": f"A job for this source is already queued or running (job #{existing['id']}, status: {existing['status']})",
                }
            ),
            409,
        )

    extensions = [v.strip().lstrip(".") for v in ext_raw.split(",") if v.strip()]
    job_id = indexing_store.create_job(
        IndexJobConfig(
            root_path=root.as_posix(),
            repository=repository,
            repository_path=repository_path,
            repository_url_base=repository_url_base,
            extensions=extensions,
            index_mode=index_mode,
        )
    )
    indexing_worker.start()
    return jsonify({"ok": True, "job_id": job_id})


@app.route("/api/indexing/run-all", methods=["POST"])
def api_indexing_run_all():
    with _run_all_lock:
        if _run_all_state["running"]:
            return (
                jsonify({"ok": False, "error": "Run-all is already in progress"}),
                409,
            )
    t = threading.Thread(target=_run_all_worker, daemon=True, name="run-all")
    t.start()
    return jsonify(
        {
            "ok": True,
            "message": f"Run-all started for {len(get_all_index_sources())} sources",
        }
    )


@app.route("/api/indexing/stop", methods=["POST"])
def api_indexing_stop():
    active = indexing_store.get_active_job()
    if not active:
        return jsonify({"ok": False, "error": "No active indexing job"}), 404

    indexing_store.request_cancel(int(active["id"]))
    return jsonify(
        {"ok": True, "job_id": int(active["id"]), "message": "Cancel requested"}
    )


@app.route("/api/indexing/status")
def api_indexing_status():
    return jsonify(_build_status_payload())


@app.route("/api/indexing/clear/solr", methods=["POST"])
def api_clear_solr():
    """Delete all documents from Solr."""
    try:
        solr.delete(q="*:*")
        solr.commit()
        return jsonify({"ok": True, "message": "Solr index cleared"})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/indexing/clear/qdrant", methods=["POST"])
def api_clear_qdrant():
    """Drop and recreate the Qdrant semantic collection."""
    if semantic_service is None:
        return jsonify({"ok": False, "error": "Semantic service unavailable"}), 503
    try:
        semantic_service.clear_collection()
        return jsonify({"ok": True, "message": "Qdrant collection cleared"})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/indexing/clear/sqlite", methods=["POST"])
def api_clear_sqlite():
    """Wipe all jobs and indexed-file history from SQLite."""
    try:
        indexing_store.clear_all_data()
        _notify_clients()
        return jsonify({"ok": True, "message": "SQLite history cleared"})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/indexing/clear/all", methods=["POST"])
def api_clear_all():
    """Clear Solr, Qdrant and SQLite in one shot."""
    errors: list[str] = []

    try:
        solr.delete(q="*:*")
        solr.commit()
    except Exception as exc:
        errors.append(f"Solr: {exc}")

    if semantic_service is not None:
        try:
            semantic_service.clear_collection()
        except Exception as exc:
            errors.append(f"Qdrant: {exc}")

    try:
        indexing_store.clear_all_data()
        _notify_clients()
    except Exception as exc:
        errors.append(f"SQLite: {exc}")

    if errors:
        return jsonify({"ok": False, "errors": errors}), 500
    return jsonify({"ok": True, "message": "All data cleared (Solr + Qdrant + SQLite)"})


# ── RAG Chat ───────────────────────────────────────────────────────────────────


@app.route("/chat")
def chat_page():
    return render_template(
        "chat.html",
        rag_search_mode=cfg.RAG_SEARCH_MODE,
        rag_top_k=cfg.RAG_TOP_K,
    )


@app.route("/api/chat/status")
def api_chat_status():
    """Check availability of the proxy chat endpoint."""
    client = ProxyChatClient(url=cfg.PROXY_CHAT_URL, timeout=5)
    available = client.is_available()
    return jsonify({"available": available, "url": cfg.PROXY_CHAT_URL})


@app.route("/api/chat", methods=["POST"])
def api_chat():
    """RAG chat endpoint — retrieves context then streams LM Studio response via SSE."""
    body = request.get_json(force=True, silent=True) or {}
    user_message: str = str(body.get("message", "")).strip()
    history: list = body.get("history", [])  # [{role, content}]
    top_k: int = int(body.get("top_k", cfg.RAG_TOP_K))

    if not user_message:
        return jsonify({"error": "message is required"}), 400

    def stream():
        app.logger.info(
            "api_chat: user=%r top_k=%d proxy=%r",
            user_message[:80],
            top_k,
            cfg.PROXY_CHAT_URL,
        )
        # ── 1. Retrieve context ────────────────────────────────────────────
        context_docs: list[dict] = []
        try:
            if semantic_service is not None:
                app.logger.debug(
                    "RAG: searching semantic index, top_k=%d min_score=%s",
                    top_k,
                    cfg.SEMANTIC_MIN_SCORE,
                )
                items = semantic_service.search(
                    query=user_message,
                    rows=top_k,
                    min_score=cfg.SEMANTIC_MIN_SCORE,
                )
                context_docs = _semantic_to_docs(items)
                context_docs = _semantic_sql_filter(user_message, context_docs)
                app.logger.info(
                    "RAG: retrieved %d docs (scores: %s)",
                    len(context_docs),
                    [
                        round(d.get("semantic_score") or d.get("score") or 0, 4)
                        for d in context_docs
                    ],
                )
            else:
                app.logger.warning(
                    "RAG: semantic_service is None — no context will be retrieved"
                )
        except Exception as exc:
            app.logger.error("RAG context retrieval error: %s", exc, exc_info=True)

        # Emit context event first
        ctx_payload = [
            {
                "file_path": d.get("file_path", ""),
                "rel_path": d.get("rel_path", "") or d.get("file_path", ""),
                "file": d.get("title") or d.get("rel_path") or d.get("file_path", ""),
                "text": str(d.get("content", "") or d.get("text", ""))[:600],
                "score": d.get("semantic_score") or d.get("score"),
            }
            for d in context_docs
        ]
        yield f"data: {json.dumps({'type': 'context', 'docs': ctx_payload})}\n\n"

        # ── 2. Build prompt ────────────────────────────────────────────────
        context_text = ""
        for idx, doc in enumerate(context_docs, start=1):
            fname = (
                doc.get("title")
                or doc.get("rel_path")
                or doc.get("file_path", f"doc{idx}")
            )
            snippet = str(doc.get("content", "") or doc.get("text", ""))[:1200]
            context_text += f"\n[{idx}] {fname}\n{snippet}\n"

        system_content = cfg.RAG_SYSTEM_PROMPT
        if context_text:
            system_content += (
                f"\n\n---\nContext documents:\n{context_text.strip()}\n---"
            )
        else:
            system_content += (
                "\n\n---\nNo relevant context documents were found in the index for this query. "
                "Do NOT answer from your own knowledge. Inform the user that no relevant documents were found.\n---"
            )

        messages: list[dict] = [{"role": "system", "content": system_content}]
        # Append conversation history (guard role values)
        for turn in history:
            if isinstance(turn, dict) and turn.get("role") in ("user", "assistant"):
                messages.append(
                    {"role": turn["role"], "content": str(turn.get("content", ""))}
                )
        messages.append({"role": "user", "content": user_message})

        # ── 3. Stream LLM response via proxy ──────────────────────────────
        app.logger.info(
            "ProxyChat: url=%r model=%r messages=%d",
            cfg.PROXY_CHAT_URL,
            cfg.PROXY_CHAT_MODEL_ID,
            len(messages),
        )
        # Extract system message; pass remaining turns to proxy
        proxy_turns = [m for m in messages if m["role"] != "system"]
        client = ProxyChatClient(url=cfg.PROXY_CHAT_URL, timeout=cfg.PROXY_CHAT_TIMEOUT)
        chunk_count = 0
        try:
            for chunk in client.chat_stream(
                system_prompt=system_content,
                messages=proxy_turns,
                model_id=cfg.PROXY_CHAT_MODEL_ID,
                model_name=cfg.PROXY_CHAT_MODEL_NAME,
            ):
                chunk_count += 1
                yield f"data: {json.dumps({'type': 'token', 'text': chunk})}\n\n"
            app.logger.info("ProxyChat: streaming complete, chunks=%d", chunk_count)
        except ProxyChatError as exc:
            app.logger.error("ProxyChat error: %s", exc)
            yield f"data: {json.dumps({'type': 'error', 'text': str(exc)})}\n\n"
        except Exception as exc:
            app.logger.error("ProxyChat unexpected error: %s", exc, exc_info=True)
            yield f"data: {json.dumps({'type': 'error', 'text': 'LLM error: ' + str(exc)})}\n\n"

        yield "data: [DONE]\n\n"

    return Response(
        stream(),
        mimetype="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


if __name__ == "__main__":
    signal.signal(signal.SIGINT, _handle_shutdown_signal)
    signal.signal(signal.SIGTERM, _handle_shutdown_signal)
    socketio.run(
        app,
        debug=True,
        port=cfg.FLASK_PORT,
        allow_unsafe_werkzeug=True,
        use_reloader=False,
    )
