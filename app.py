"""
Flask Solr Search Application
Supports multilingual document search: English, Japanese, Vietnamese
"""

from __future__ import annotations
import math
import logging
from urllib.parse import urlencode, urlparse, parse_qs, urlunparse
from flask import Flask, render_template, request, jsonify, Response
from werkzeug.middleware.proxy_fix import ProxyFix
import pysolr
import config as cfg
from werkzeug.middleware.dispatcher import DispatcherMiddleware

app = Flask(__name__)

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


# ── Jinja2 custom filters ──────────────────────────────────────────────────────


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
    new_query = urlencode({k: v[0] for k, v in qs.items()})
    return urlunparse(parsed._replace(query=new_query))


@app.template_filter("set_param")
def set_param(url: str, param: str, value: str) -> str:
    """Return the URL with the specified query param set to a new value."""
    parsed = urlparse(url)
    qs = parse_qs(parsed.query, keep_blank_values=True)
    qs[param] = [value]
    new_query = urlencode({k: v[0] for k, v in qs.items()})
    return urlunparse(parsed._replace(query=new_query))


@app.template_filter("add_facet")
def add_facet(url: str, field: str, value: str) -> str:
    """Add or update a facet parameter and reset page to 1."""
    parsed = urlparse(url)
    qs = parse_qs(parsed.query, keep_blank_values=True)
    qs[field] = [value]
    qs["page"] = ["1"]
    new_query = urlencode({k: v[0] for k, v in qs.items()})
    return urlunparse(parsed._replace(query=new_query))


# ── Solr client ────────────────────────────────────────────────────────────────
solr = pysolr.Solr(cfg.SOLR_URL, timeout=cfg.SOLR_TIMEOUT, always_commit=False)


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
    boost_func = args.get("bf", "").strip()
    debug_mode = args.get("debug", "") == "1"

    # --- Filters ---
    language = args.get("lang", "").strip()
    file_type = args.get("file_type", "").strip()
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
        boost_func=boost_func,
        debug_mode=debug_mode,
        language=language,
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
            q = " ".join(f'"{term}"' if not term.startswith("-") else term for term in q.split())

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

    # Build filter queries list
    fqs: list[str] = []
    if p["language"]:
        fqs.append(f'{cfg.FIELD_LANGUAGE}:"{p["language"]}"')
    if p["file_type"]:
        fqs.append(f'{cfg.FIELD_FILE_TYPE}:"{p["file_type"]}"')

    # Date range
    df = p["date_from"] or "*"
    dt = p["date_to"] or "*"
    if p["date_from"] or p["date_to"]:
        fqs.append(f"{cfg.FIELD_DATE}:[{df}T00:00:00Z TO {dt}T23:59:59Z]")

    if p["extra_fq"]:
        fqs.append(p["extra_fq"])

    params: dict = {
        "defType": p["parser"],
        "q.op": p["operator"],
        "sort": p["sort_val"],
        "rows": p["rows"],
        "start": p["start"],
        # Facets
        "facet": "true",
        "facet.field": cfg.FACET_FIELDS,
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
        params["group.limit"] = 3  # top 3 docs per group
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
        "q": q,
        "params": params,
    }


# ── Routes ─────────────────────────────────────────────────────────────────────


@app.route("/")
def index():
    return render_template(
        "index.html",
        languages=cfg.LANGUAGES,
        file_types=cfg.FILE_TYPES,
        sort_options=cfg.SORT_OPTIONS,
        default_rows=cfg.DEFAULT_ROWS,
    )


@app.route("/search")
def search():
    p = parse_form(request.args)
    data = execute_search(p) if p["query"] or request.args.get("q") else None
    return render_template(
        "index.html",
        languages=cfg.LANGUAGES,
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
    if not p["query"] and not request.args.get("q"):
        return jsonify({"docs": [], "num_found": 0, "facets": {}, "error": None})
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


if __name__ == "__main__":
    app.run(debug=True, port=5100)
