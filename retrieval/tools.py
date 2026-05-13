"""Tool definitions and executors for the agentic RAG loop.

Each tool has:
  - An OpenAI-compatible schema (for the ``tools`` parameter of chat/completions)
  - An executor function called by ``execute_tool()``

Tools:
  search_documents    — semantic vector search via Qdrant
  get_graph_neighbors — code relationship graph via Neo4j
"""

from __future__ import annotations

import json
import logging

import config as cfg
import services
from retrieval.proxy_chat import LLMClient
from search.result import _semantic_sql_filter, _semantic_to_docs

logger = logging.getLogger(__name__)

# ── OpenAI tool schemas ───────────────────────────────────────────────────────

TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "search_documents",
            "description": (
                "Search the indexed knowledge base using semantic similarity. "
                "Use this to find relevant source code, documentation, configuration "
                "files, or any text content related to the question. "
                "Prefer this tool for conceptual or natural-language queries."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural-language search query.",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Number of results to return (1–20). Default 5.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_graph_neighbors",
            "description": (
                "Explore the code knowledge graph to answer questions about RELATIONSHIPS. "
                "USE THIS TOOL — not search_documents — when the question asks: "
                "which functions/services READ or WRITE a table; "
                "who calls a function or what a function calls; "
                "what tables/APIs a service uses; "
                "impact analysis or call-chain tracing. "
                "Pass the bare entity name (e.g. 'AUDIT_LOG', 'EMPLOYEES', 'UserService', 'OrderController')."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "qualified_name": {
                        "type": "string",
                        "description": (
                            "Entity name to look up. Prefer the bare name without schema/namespace prefix "
                            "(e.g. 'EMPLOYEES' rather than 'HR.EMPLOYEES' or 'Table:repo:EMPLOYEES'). "
                            "Short names like 'UserService', 'EMPLOYEES', 'OrderController' work best. "
                            "Dot-qualified names (e.g. 'HR.EMPLOYEES') and colon-qualified names "
                            "(e.g. 'Table:repo:EMPLOYEES') are also accepted."
                        ),
                    },
                    "direction": {
                        "type": "string",
                        "enum": ["both", "outgoing", "incoming"],
                        "description": "Edge direction to traverse. Default 'both'.",
                    },
                    "label": {
                        "type": "string",
                        "description": (
                            "Optional: restrict the starting node to this Neo4j label "
                            "(e.g. 'Table', 'Function', 'ApiEndpoint', 'Workflow', 'Task', 'Service', 'Document')."
                        ),
                    },
                },
                "required": ["qualified_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_graph_nodes",
            "description": (
                "Search for nodes in the knowledge graph by partial name. "
                "Use this BEFORE get_graph_neighbors when you don't know the exact entity name. "
                "Returns matching node names, labels, and qualified names."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Partial name to search for (case-insensitive).",
                    },
                    "label": {
                        "type": "string",
                        "description": (
                            "Optional node label filter "
                            "(e.g. 'Table', 'Function', 'Service', 'Workflow', 'Task')."
                        ),
                    },
                },
                "required": ["query"],
            },
        },
    },
]

# ── Executors ─────────────────────────────────────────────────────────────────


def _generate_hypothetical_doc(query: str) -> str | None:
    """Generate a hypothetical answer document for HyDE embedding."""
    try:
        client = LLMClient(base_url=cfg.LLM_BASE_URL, timeout=20)
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a code assistant. Write a short, realistic code snippet "
                    "(≤120 words) that would answer the question below. "
                    "Output only raw code or a brief technical description — no explanations."
                ),
            },
            {"role": "user", "content": query},
        ]
        response = client.chat_complete(
            messages=messages, model=cfg.LLM_MODEL, max_tokens=180
        )
        choices = response.get("choices") or []
        text = choices[0].get("message", {}).get("content", "") if choices else ""
        return text.strip() or None
    except Exception as exc:
        logger.debug("HyDE generation failed: %s", exc)
        return None


def _exec_search_documents(query: str, top_k: int = 5) -> tuple[dict, list[dict], dict]:
    top_k = max(1, min(int(top_k), 20))
    if services.retrieval_service is None:
        return {"error": "Retrieval service unavailable.", "docs": []}, [], {}
    try:
        hyde_text = _generate_hypothetical_doc(query)
        items = services.retrieval_service.search(
            query=query,
            rows=top_k,
            min_score=cfg.SEMANTIC_MIN_SCORE,
            hyde_text=hyde_text,
        )
        raw_docs = _semantic_to_docs(items)
        raw_docs = _semantic_sql_filter(query, raw_docs)
        result_docs = [
            {
                "file": d.get("title") or d.get("rel_path") or d.get("file_path", ""),
                "file_path": d.get("file_path", ""),
                "text": str(d.get("content") or d.get("text", ""))[:3000],
                "score": round(
                    float(d.get("semantic_score") or d.get("score") or 0), 4
                ),
            }
            for d in raw_docs
        ]
        return {"docs": result_docs}, result_docs, {}
    except Exception as exc:
        logger.error("search_documents failed: %s", exc)
        return {"error": str(exc), "docs": []}, [], {}


def _exec_get_graph_neighbors(
    qualified_name: str, direction: str = "both", label: str = ""
) -> tuple[dict, list[dict], dict]:
    if services.graph_service is None or not services.graph_service.available:
        return {"error": "Graph database unavailable.", "edges": []}, [], {}
    try:
        return services.graph_service.get_neighbors(
            qualified_name=qualified_name,
            direction=direction,
            label=label,
        )
    except Exception as exc:
        logger.error("get_graph_neighbors failed: %s", exc)
        return {"error": str(exc), "edges": []}, [], {}


def _exec_search_graph_nodes(
    query: str, label: str = ""
) -> tuple[dict, list[dict], dict]:
    if services.graph_service is None or not services.graph_service.available:
        return {"error": "Graph database unavailable.", "nodes": []}, [], {}
    try:
        return services.graph_service.search_nodes(query=query, label=label)
    except Exception as exc:
        logger.error("search_graph_nodes failed: %s", exc)
        return {"error": str(exc), "nodes": []}, [], {}


# ── Dispatch ───────────────────────────────────────────────────────

_EXECUTORS = {
    "search_documents": _exec_search_documents,
    "get_graph_neighbors": _exec_get_graph_neighbors,
    "search_graph_nodes": _exec_search_graph_nodes,
}


def execute_tool(name: str, arguments: str) -> tuple[dict, list[dict], dict]:
    """Execute a tool by name and JSON-string arguments.

    Returns ``(result_dict, docs_list, graph_dict)`` where:
      - ``docs_list`` — document dicts for LLM context
      - ``graph_dict`` — vis-network payload ``{nodes, edges}`` for the UI sidebar
        (empty dict for non-graph tools)
    """
    fn = _EXECUTORS.get(name)
    if fn is None:
        err = {"error": f"Unknown tool: {name!r}"}
        return err, [], {}

    try:
        args: dict = json.loads(arguments) if arguments.strip() else {}
    except json.JSONDecodeError as exc:
        err = {"error": f"Invalid tool arguments JSON: {exc}"}
        return err, [], {}

    logger.info("execute_tool: %s args=%r", name, args)
    try:
        result_tuple = fn(**args)
    except Exception as exc:
        logger.error("execute_tool: %s raised %s", name, exc, exc_info=True)
        return {"error": str(exc)}, [], {}

    # Guard against executors returning a malformed tuple (wrong length or
    # None docs) so callers can always safely do accumulated_docs.extend(docs).
    if not isinstance(result_tuple, tuple) or len(result_tuple) < 3:
        logger.error(
            "execute_tool: %s returned unexpected value type=%s",
            name, type(result_tuple),
        )
        return {"error": "Tool returned unexpected result format"}, [], {}

    r_dict, docs, graph = result_tuple
    if docs is None:
        docs = []
    return r_dict, docs, graph
