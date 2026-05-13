"""Result helpers: convert semantic hits, SQL filtering, facets, grouping."""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import config as cfg
from retrieval.retrieval_service import SQL_HINT_TERMS


def _semantic_to_docs(items: list[dict]) -> list[dict]:
    docs = []
    for idx, item in enumerate(items):
        fp = str(item.get("file_path", ""))
        rel = str(item.get("rel_path", "")).replace("\\", "/")
        title = Path(fp).name if fp else f"semantic-{idx}"
        repo_url_base = item.get("repository_url_base", "")

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

    if identifiers:
        return filtered

    return filtered if filtered else docs


def _is_sql_like_query(query: str) -> bool:
    terms = [t.lower() for t in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", query)]
    return any(t in SQL_HINT_TERMS for t in terms)


def _compute_facets(docs: list[dict]) -> dict:
    """Compute facet counts from a list of result docs."""
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
