"""Full-text Solr search: build params and execute query."""
from __future__ import annotations

import logging
import math

import pysolr

import config as cfg
import services
from search.params import build_qf_string

logger = logging.getLogger(__name__)


def build_solr_params(p: dict) -> tuple[str, dict]:
    """Build the Solr query string and params dict from parsed form params."""
    q = p["query"] or "*:*"

    try:
        prox = int(p["proximity"])
    except (ValueError, TypeError):
        prox = 0

    if prox > 0 and q != "*:*":
        q_clean = q.strip('"')
        q = f'"{q_clean}"~{prox}'
    else:
        if q != "*:*" and p["exact_terms"] and not p["phrase_mode"]:
            q = " ".join(
                f'"{term}"' if not term.startswith("-") else term for term in q.split()
            )
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

    df = p["date_from"] or "*"
    dt = p["date_to"] or "*"
    if p["date_from"] or p["date_to"]:
        fqs.append(f"{cfg.FIELD_DATE}:[{df}T00:00:00Z TO {dt}T23:59:59Z]")

    if p["extra_fq"]:
        fqs.append(p["extra_fq"])

    facet_fields_ex = [f"{{!ex={f}}}{f}" for f in cfg.FACET_FIELDS]

    params: dict = {
        "defType": p["parser"],
        "q.op": p["operator"],
        "sort": p["sort_val"],
        "rows": p["rows"],
        "start": p["start"],
        "facet": "true",
        "facet.field": facet_fields_ex,
        "facet.mincount": 1,
        "facet.limit": 20,
        "hl": "true" if p["hl_enabled"] else "false",
        "hl.fl": cfg.HL_FIELDS,
        "hl.snippets": cfg.HL_SNIPPETS,
        "hl.fragsize": cfg.HL_FRAG_SIZE,
        "hl.simple.pre": cfg.HL_PRE_TAG,
        "hl.simple.post": cfg.HL_POST_TAG,
        "hl.requireFieldMatch": "false",
        "hl.encoder": "html",
    }

    if p["parser"] == "edismax":
        params["qf"] = build_qf_string()
        params["mm"] = p["mm_val"] if p["mm_val"] else "1"

    if p["group_by"]:
        params["group"] = "true"
        params["group.field"] = p["group_by"]
        params["group.limit"] = 10
        params["group.ngroups"] = "true"
        params["group.sort"] = "score desc"

    if p["boost_func"]:
        params["bf"] = p["boost_func"]

    if fqs:
        params["fq"] = fqs

    if p["debug_mode"]:
        params["debugQuery"] = "true"

    return q, params


def execute_fulltext_search(p: dict) -> dict:
    """Run full-text Solr search only."""
    q, params = build_solr_params(p)

    try:
        results = services.solr.search(q, **params)
    except pysolr.SolrError as exc:
        logger.error("Solr error: %s", exc)
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

    facets: dict[str, list] = {}
    raw_ff = getattr(results, "facets", {}).get("facet_fields", {})
    for field, items in raw_ff.items():
        pairs = []
        it = iter(items)
        for val in it:
            cnt = next(it, 0)
            pairs.append((val, cnt))
        facets[field] = pairs

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
