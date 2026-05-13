"""Blueprint: chat routes (/chat, /api/chat, /api/chat/status, /api/chat/history)."""
from __future__ import annotations

import uuid

from flask import Blueprint, Response, jsonify, render_template, request

import config as cfg
import services
from retrieval.rag import stream_rag_response
from retrieval.proxy_chat import LLMClient

retrieval_bp = Blueprint("retrieval", __name__)

_MAX_HISTORY = 100  # messages returned per session


def _session_id() -> str | None:
    """Extract and validate session UUID from the X-Session-ID request header."""
    sid = (request.headers.get("X-Session-ID") or "").strip()
    if not sid:
        return None
    try:
        return str(uuid.UUID(sid))  # validates format
    except ValueError:
        return None


@retrieval_bp.route("/chat")
def chat_page():
    return render_template(
        "retrieval.html",
        rag_top_k=cfg.RAG_TOP_K,
    )


@retrieval_bp.route("/api/chat/status")
def api_chat_status():
    """Check availability of the proxy chat endpoint."""
    client = LLMClient(base_url=cfg.LLM_BASE_URL, timeout=5)
    available = client.is_available()
    return jsonify({"available": available, "url": cfg.LLM_BASE_URL})


@retrieval_bp.route("/api/chat", methods=["POST"])
def api_chat():
    """RAG chat endpoint — retrieves context then streams LM Studio response via SSE."""
    body = request.get_json(force=True, silent=True) or {}
    user_message: str = str(body.get("message", "")).strip()
    history: list = body.get("history", [])
    top_k: int = int(body.get("top_k", cfg.RAG_TOP_K))

    if not user_message:
        return jsonify({"error": "message is required"}), 400

    return Response(
        stream_rag_response(user_message, history, top_k),
        mimetype="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


# ── Chat history endpoints ─────────────────────────────────────────────────────

@retrieval_bp.route("/api/chat/history", methods=["GET"])
def api_chat_history_get():
    """Return saved messages for a session.

    Query param: session_id (UUID)
    Returns: {messages: [...], session_id: str}
    """
    sid = request.args.get("session_id", "").strip()
    if not sid:
        return jsonify({"error": "session_id is required"}), 400
    try:
        sid = str(uuid.UUID(sid))
    except ValueError:
        return jsonify({"error": "Invalid session_id format"}), 400

    store = services.chat_store
    if store is None:
        return jsonify({"error": "Chat store unavailable"}), 503

    messages = store.get_history(sid, limit=_MAX_HISTORY)
    return jsonify({"session_id": sid, "messages": messages})


@retrieval_bp.route("/api/chat/history", methods=["POST"])
def api_chat_history_post():
    """Append a message to a session's history.

    Body (JSON):
      session_id  UUID string (required)
      role        "user" | "assistant" (required)
      content     Message text (required)
      events      list of {type, ...} SSE events (optional)
      sources     list of doc objects (optional)
      graph       vis-network {nodes, edges} (optional)
    """
    body = request.get_json(force=True, silent=True) or {}
    sid = body.get("session_id", "").strip()
    if not sid:
        return jsonify({"error": "session_id is required"}), 400
    try:
        sid = str(uuid.UUID(sid))
    except ValueError:
        return jsonify({"error": "Invalid session_id format"}), 400

    role = body.get("role", "")
    if role not in ("user", "assistant"):
        return jsonify({"error": "role must be 'user' or 'assistant'"}), 400

    content = str(body.get("content", ""))
    events  = body.get("events",  []) or []
    sources = body.get("sources", []) or []
    graph   = body.get("graph",   {}) or {}

    store = services.chat_store
    if store is None:
        return jsonify({"error": "Chat store unavailable"}), 503

    msg_id = store.append_message(
        session_id=sid,
        role=role,
        content=content,
        events=events,
        sources=sources,
        graph=graph,
    )
    return jsonify({"id": msg_id, "session_id": sid}), 201


@retrieval_bp.route("/api/chat/history", methods=["DELETE"])
def api_chat_history_delete():
    """Delete all messages for a session.

    Query param: session_id (UUID)
    """
    sid = request.args.get("session_id", "").strip()
    if not sid:
        return jsonify({"error": "session_id is required"}), 400
    try:
        sid = str(uuid.UUID(sid))
    except ValueError:
        return jsonify({"error": "Invalid session_id format"}), 400

    store = services.chat_store
    if store is None:
        return jsonify({"error": "Chat store unavailable"}), 503

    store.clear_session(sid)
    return jsonify({"deleted": True, "session_id": sid})


@retrieval_bp.route("/api/chat/sessions", methods=["GET"])
def api_chat_sessions():
    """List all sessions ordered by most recent activity.

    Returns: {sessions: [{id, title, last_active_at, msg_count}, ...]}
    """
    store = services.chat_store
    if store is None:
        return jsonify({"error": "Chat store unavailable"}), 503
    sessions = store.list_sessions(limit=50)
    return jsonify({"sessions": sessions})

