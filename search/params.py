"""Parse HTTP request args into a normalised search-parameter dict."""
from __future__ import annotations

import config as cfg


def build_qf_string() -> str:
    """Build edismax qf string from configured query fields with boosts."""
    return " ".join(f"{field}^{boost}" for field, boost in cfg.QUERY_FIELDS.items())


def parse_form(args) -> dict:
    """Extract and sanitise search parameters from the request args/form.

    ``args`` can be a Flask ``MultiDict`` (request.args) or any mapping that
    exposes ``.get()`` and ``.getlist()``.
    """
    query = args.get("q", "").strip()

    # --- Query modifiers ---
    phrase_mode = args.get("phrase", "") == "1"
    exact_terms = args.get("exact", "") == "1"
    fuzzy = args.get("fuzzy", "0").strip()
    operator = args.get("op", cfg.DEFAULT_OPERATOR)
    parser = args.get("parser", cfg.DEFAULT_PARSER)
    search_mode = "fulltext"
    boost_func = args.get("bf", "").strip()
    debug_mode = args.get("debug", "") == "1"

    # --- Filters ---
    repository = [v.strip() for v in args.getlist("repository") if v.strip()]
    file_type = [v.strip() for v in args.getlist("file_type") if v.strip()]
    date_from = args.get("date_from", "").strip()
    date_to = args.get("date_to", "").strip()
    extra_fq = args.get("fq", "").strip()

    # --- Display ---
    sort_val = args.get("sort", "score desc")
    rows = min(int(args.get("rows", cfg.DEFAULT_ROWS)), 100)
    page = max(int(args.get("page", 1)), 1)
    start = (page - 1) * rows

    # --- Proximity ---
    proximity = args.get("proximity", "0").strip()

    # --- Minimum Should Match ---
    mm_val = args.get("mm", "").strip()

    # --- Group by ---
    group_by = args.get("group_by", "").strip()

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
