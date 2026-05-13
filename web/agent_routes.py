"""Agent routes — Table-Centric Agentic RAG impact query API.

Blueprint: /api/agent/

Endpoints:
  GET  /table-writers        Who writes to a given table?
  GET  /table-readers        Who reads from a given table?
  GET  /table-impact         Full impact view for a table (writers + readers + entry points)
  GET  /service-tables       All tables a service reads or writes
  GET  /api-impact           API endpoint → functions → tables
  GET  /tables               All Table nodes
  GET  /graph/node-labels    All distinct labels present in the graph
  POST /graph/nodes          Manually create a node
  POST /graph/edges          Manually create an edge
"""
from __future__ import annotations

import logging
from functools import wraps
from typing import Any

from flask import Blueprint, jsonify, request

import services

try:
    from graph.db import schema as S
except ImportError:
    S = None  # type: ignore  # graph module not installed

logger = logging.getLogger(__name__)

agent_bp = Blueprint("agent", __name__, url_prefix="/api/agent")


def _require_graph(fn):
    """Decorator: return 503 if graph is unavailable."""
    @wraps(fn)
    def _wrapper(*args, **kwargs):
        if services.graph_service is None or not services.graph_service.available:
            return jsonify({"error": "Graph database is not available"}), 503
        return fn(*args, **kwargs)
    return _wrapper


# ── Endpoints ─────────────────────────────────────────────────────────────────

@agent_bp.get("/table-writers")
@_require_graph
def table_writers():
    """Functions that WRITE to a given table."""
    table = request.args.get("table", "").strip()
    if not table:
        return jsonify({"error": "table parameter required"}), 400
    repo = request.args.get("repository", "").strip()
    results = services.graph_service.get_table_writers(table, repo)
    return jsonify({"table": table.upper(), "writers": results, "count": len(results)})


@agent_bp.get("/table-readers")
@_require_graph
def table_readers():
    """Functions that READ from a given table."""
    table = request.args.get("table", "").strip()
    if not table:
        return jsonify({"error": "table parameter required"}), 400
    repo = request.args.get("repository", "").strip()
    results = services.graph_service.get_table_readers(table, repo)
    return jsonify({"table": table.upper(), "readers": results, "count": len(results)})


@agent_bp.get("/table-impact")
@_require_graph
def table_impact():
    """Full blast radius for a table (writers, readers, API endpoints, cron jobs)."""
    table = request.args.get("table", "").strip()
    if not table:
        return jsonify({"error": "table parameter required"}), 400
    return jsonify(services.graph_service.get_table_impact(table))


@agent_bp.get("/service-tables")
@_require_graph
def service_tables():
    """All tables a given service reads or writes."""
    service = request.args.get("service", "").strip()
    if not service:
        return jsonify({"error": "service parameter required"}), 400
    rows = services.graph_service.get_service_tables(service)
    return jsonify({"service": service, "tables": rows, "count": len(rows)})


@agent_bp.get("/api-impact")
@_require_graph
def api_impact():
    """Given an API endpoint (method + path), find all tables it may read/write."""
    method = request.args.get("method", "").strip().upper()
    path = request.args.get("path", "").strip()
    if not method or not path:
        return jsonify({"error": "method and path parameters required"}), 400
    rows = services.graph_service.get_api_impact(method, path)
    return jsonify({"method": method, "path": path, "impact": rows})


@agent_bp.get("/tables")
@_require_graph
def list_tables():
    """List all Table nodes in the graph."""
    repository = request.args.get("repository", "").strip()
    tables = services.graph_service.list_tables(repository)
    return jsonify({"tables": tables, "count": len(tables)})


@agent_bp.get("/graph/node-labels")
@_require_graph
def node_labels():
    """Return all distinct node labels in the graph."""
    labels = services.graph_service.list_node_labels()
    return jsonify({"labels": labels})


@agent_bp.post("/graph/nodes")
@_require_graph
def create_node():
    """Manually create or merge a graph node.

    Body (JSON):
      label      Node label (string)
      name       Human-readable name
      properties Additional properties dict (optional)
    """
    body = request.get_json(silent=True) or {}
    label = body.get("label", "").strip()
    name = body.get("name", "").strip()
    if not label or not name:
        return jsonify({"error": "label and name are required"}), 400

    try:
        from graph.db.entities import GraphNode, ExtractionResult
        from graph.db.writer import GraphWriter
        from graph.db.client import get_client

        props = dict(body.get("properties") or {})
        repository = props.get("repository", "manual")
        qname = f"{label}:{repository}:{name}"
        props.update({
            "qualified_name": qname,
            "name": name,
            "repository": repository,
            "source": "manual",
        })

        node = GraphNode(label=label, key="qualified_name", key_value=qname, properties=props, source="manual")
        result = ExtractionResult(source_file="manual", extractor_name="manual")
        result.nodes.append(node)

        writer = GraphWriter(get_client())
        writer.write(result)
        return jsonify({"created": True, "qualified_name": qname}), 201
    except Exception as exc:
        logger.error("create_node failed: %s", exc, exc_info=True)
        return jsonify({"error": str(exc)}), 500


@agent_bp.post("/graph/edges")
@_require_graph
def create_edge():
    """Manually create or merge a graph edge.

    Body (JSON):
      from_label       Label of source node
      from_key_value   qualified_name of source node
      to_label         Label of target node
      to_key_value     qualified_name of target node
      rel_type         Relationship type (e.g. WRITES_TO)
      properties       Additional properties dict (optional)
    """
    body = request.get_json(silent=True) or {}
    required = ("from_label", "from_key_value", "to_label", "to_key_value", "rel_type")
    missing = [k for k in required if not body.get(k)]
    if missing:
        return jsonify({"error": f"Missing required fields: {missing}"}), 400

    try:
        from graph.db.entities import GraphEdge, ExtractionResult
        from graph.db.writer import GraphWriter
        from graph.db.client import get_client

        edge = GraphEdge(
            from_label=body["from_label"],
            from_key="qualified_name",
            from_key_value=body["from_key_value"],
            to_label=body["to_label"],
            to_key="qualified_name",
            to_key_value=body["to_key_value"],
            rel_type=body["rel_type"],
            properties=dict(body.get("properties") or {}),
        )
        result = ExtractionResult(source_file="manual", extractor_name="manual")
        result.edges.append(edge)

        writer = GraphWriter(get_client())
        writer.write(result)
        return jsonify({"created": True}), 201
    except Exception as exc:
        logger.error("create_edge failed: %s", exc, exc_info=True)
        return jsonify({"error": str(exc)}), 500
