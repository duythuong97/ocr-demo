"""Knowledge routes: CRUD API for manual knowledge (nodes, edges, text).

Blueprint: /api/knowledge/

Also serves:  GET /knowledge  → knowledge.html page
"""
from __future__ import annotations

import io
import logging
from functools import wraps

from flask import Blueprint, Response, jsonify, render_template, request

import services

logger = logging.getLogger(__name__)

knowledge_bp = Blueprint("knowledge", __name__)


def _svc():
    return getattr(services, "knowledge_service", None)


def _require_svc(fn):
    @wraps(fn)
    def _w(*a, **kw):
        if _svc() is None:
            return jsonify({"error": "KnowledgeService not initialised"}), 503
        return fn(*a, **kw)
    return _w


# ── Page ──────────────────────────────────────────────────────────────────────

@knowledge_bp.get("/knowledge")
def knowledge_page():
    return render_template("knowledge.html", active_page="knowledge")


# ── Nodes ─────────────────────────────────────────────────────────────────────

@knowledge_bp.get("/api/knowledge/nodes")
@_require_svc
def list_nodes():
    label = request.args.get("label", "").strip()
    q = request.args.get("q", "").strip()
    limit = min(int(request.args.get("limit", 200)), 1000)
    rows = _svc().list_nodes(label=label, q=q, limit=limit)
    return jsonify({"nodes": rows, "count": len(rows)})


@knowledge_bp.post("/api/knowledge/nodes")
@_require_svc
def create_node():
    body = request.get_json(silent=True) or {}
    label = body.get("label", "").strip()
    name = body.get("name", "").strip()
    if not label or not name:
        return jsonify({"error": "label and name are required"}), 400
    repository = body.get("repository", "manual").strip() or "manual"
    properties = dict(body.get("properties") or {})
    result = _svc().create_node(label, name, repository, properties)
    return jsonify(result), 201


@knowledge_bp.put("/api/knowledge/nodes/<int:node_id>")
@_require_svc
def update_node(node_id: int):
    body = request.get_json(silent=True) or {}
    name = body.get("name", "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400
    repository = body.get("repository", "manual").strip() or "manual"
    properties = dict(body.get("properties") or {})
    ok = _svc().update_node(node_id, name, repository, properties)
    if not ok:
        return jsonify({"error": "not found"}), 404
    return jsonify({"updated": True, "id": node_id})


@knowledge_bp.delete("/api/knowledge/nodes/<int:node_id>")
@_require_svc
def delete_node(node_id: int):
    ok = _svc().delete_node(node_id)
    if not ok:
        return jsonify({"error": "not found"}), 404
    return jsonify({"deleted": True})


# ── Edges ─────────────────────────────────────────────────────────────────────

@knowledge_bp.get("/api/knowledge/edges")
@_require_svc
def list_edges():
    rel_type = request.args.get("rel_type", "").strip()
    q = request.args.get("q", "").strip()
    limit = min(int(request.args.get("limit", 200)), 1000)
    rows = _svc().list_edges(rel_type=rel_type, q=q, limit=limit)
    return jsonify({"edges": rows, "count": len(rows)})


@knowledge_bp.post("/api/knowledge/edges")
@_require_svc
def create_edge():
    body = request.get_json(silent=True) or {}
    from_qname = body.get("from_qname", "").strip()
    to_qname = body.get("to_qname", "").strip()
    rel_type = body.get("rel_type", "").strip().upper()
    if not from_qname or not to_qname or not rel_type:
        return jsonify({"error": "from_qname, to_qname, rel_type are required"}), 400
    properties = dict(body.get("properties") or {})
    result = _svc().create_edge(from_qname, to_qname, rel_type, properties)
    return jsonify(result), 201


@knowledge_bp.delete("/api/knowledge/edges/<int:edge_id>")
@_require_svc
def delete_edge(edge_id: int):
    ok = _svc().delete_edge(edge_id)
    if not ok:
        return jsonify({"error": "not found"}), 404
    return jsonify({"deleted": True})


# ── Texts ─────────────────────────────────────────────────────────────────────

@knowledge_bp.get("/api/knowledge/texts")
@_require_svc
def list_texts():
    q = request.args.get("q", "").strip()
    tags = request.args.get("tags", "").strip()
    limit = min(int(request.args.get("limit", 200)), 1000)
    rows = _svc().list_texts(q=q, tags=tags, limit=limit)
    return jsonify({"texts": rows, "count": len(rows)})


@knowledge_bp.get("/api/knowledge/texts/<int:text_id>")
@_require_svc
def get_text(text_id: int):
    row = _svc().get_text(text_id)
    if not row:
        return jsonify({"error": "not found"}), 404
    return jsonify(row)


@knowledge_bp.post("/api/knowledge/texts")
@_require_svc
def create_text():
    body = request.get_json(silent=True) or {}
    title = body.get("title", "").strip()
    content = body.get("content", "").strip()
    if not title or not content:
        return jsonify({"error": "title and content are required"}), 400
    tags = body.get("tags", "").strip()
    repository = body.get("repository", "knowledge").strip() or "knowledge"
    result = _svc().create_text(title, content, tags, repository)
    return jsonify(result), 201


@knowledge_bp.put("/api/knowledge/texts/<int:text_id>")
@_require_svc
def update_text(text_id: int):
    body = request.get_json(silent=True) or {}
    title = body.get("title", "").strip()
    content = body.get("content", "").strip()
    if not title or not content:
        return jsonify({"error": "title and content are required"}), 400
    tags = body.get("tags", "").strip()
    repository = body.get("repository", "knowledge").strip() or "knowledge"
    ok = _svc().update_text(text_id, title, content, tags, repository)
    if not ok:
        return jsonify({"error": "not found"}), 404
    return jsonify({"updated": True, "id": text_id})


@knowledge_bp.delete("/api/knowledge/texts/<int:text_id>")
@_require_svc
def delete_text(text_id: int):
    ok = _svc().delete_text(text_id)
    if not ok:
        return jsonify({"error": "not found"}), 404
    return jsonify({"deleted": True})


# ── Apply ─────────────────────────────────────────────────────────────────────

@knowledge_bp.post("/api/knowledge/apply")
@_require_svc
def apply_all():
    try:
        result = _svc().apply_all()
        return jsonify({"ok": True, "result": result})
    except Exception as exc:
        logger.error("apply_all failed: %s", exc, exc_info=True)
        return jsonify({"error": str(exc)}), 500


# ── Templates ─────────────────────────────────────────────────────────────────

@knowledge_bp.get("/api/knowledge/templates")
def list_templates():
    from knowledge.service import KNOWLEDGE_TEMPLATES
    return jsonify({"templates": KNOWLEDGE_TEMPLATES})


# ── Quick Add ─────────────────────────────────────────────────────────────────

@knowledge_bp.post("/api/knowledge/quick-add")
@_require_svc
def quick_add():
    body = request.get_json(silent=True) or {}
    template_id = body.get("template_id", "").strip()
    from_name = body.get("from_name", "").strip()
    to_name = body.get("to_name", "").strip()
    repository = body.get("repository", "manual").strip() or "manual"
    if not template_id or not from_name or not to_name:
        return jsonify({"error": "template_id, from_name, to_name are required"}), 400
    try:
        result = _svc().quick_add(template_id, from_name, to_name, repository)
        return jsonify(result), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


# ── CSV Import ────────────────────────────────────────────────────────────────

@knowledge_bp.post("/api/knowledge/import/csv")
@_require_svc
def import_csv():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded (field name: 'file')"}), 400
    f = request.files["file"]
    if not f.filename or not f.filename.lower().endswith(".csv"):
        return jsonify({"error": "Only .csv files accepted"}), 400
    file_bytes = f.read()
    if len(file_bytes) > 5 * 1024 * 1024:  # 5 MB guard
        return jsonify({"error": "File too large (max 5 MB)"}), 413
    result = _svc().import_csv(file_bytes)
    return jsonify(result)


@knowledge_bp.get("/api/knowledge/import/template.csv")
def download_csv_template():
    from knowledge.service import _CSV_HEADERS
    rows = [
        _CSV_HEADERS,
        ["Function", "UserService.save", "my-repo", "WRITES_TO", "Table", "users"],
        ["Function", "OrderService.list", "my-repo", "READS_FROM", "Table", "orders"],
        ["Class", "UserRepository", "my-repo", "HAS_MAPPER", "Module", "UserMapper"],
        ["Function", "OrderProcessor.run", "my-repo", "BELONGS_TO", "BackgroundJob", "OrderJob"],
        ["Function", "NotificationService.send", "my-repo", "CALLS_API", "ApiCall", "/api/notifications"],
    ]
    buf = io.StringIO()
    import csv
    csv.writer(buf).writerows(rows)
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": 'attachment; filename="knowledge_template.csv"'},
    )
