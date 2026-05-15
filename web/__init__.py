"""Application factory: create_app() wires together all services and blueprints."""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

import pysolr
from flask import Flask
from flask_socketio import SocketIO
from werkzeug.middleware.dispatcher import DispatcherMiddleware
from werkzeug.middleware.proxy_fix import ProxyFix
from flask import Response

import config as cfg
import services
from infra.embedding import Embedder
from infra.qdrant_store import QdrantStore
from infra.solr_store import SolrStore
from ingest.store import IndexingStateStore
from ingest.worker import IndexingWorker
from ingest.run_all import _build_status_payload, _notify_clients

from web.filters import register_filters
from web.search_routes import search_bp
from web.ingest_routes import ingest_bp
from web.retrieval_routes import retrieval_bp
from web.knowledge_routes import knowledge_bp

# ── Graph client (optional) ───────────────────────────────────────────────────
try:
    from graph.db.client import (
        init_client as _graph_init_client,
    )
    _GRAPH_IMPORTS_OK = True
except ImportError:
    _GRAPH_IMPORTS_OK = False


def create_app() -> tuple[Flask, SocketIO]:
    """Create and configure the Flask application.

    Returns ``(app, socketio)`` so that ``app.py`` can call ``socketio.run()``.
    """
    app = Flask(__name__, template_folder="../templates", static_folder="../static")
    socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

    # ── Subdirectory & Proxy Handling ──────────────────────────────────────────
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

    if cfg.APP_PREFIX and getattr(cfg, "SIMULATE_SUBDIRECTORY", False):
        prefix = "/" + cfg.APP_PREFIX.strip("/")

        def root_app(environ, start_response):
            if (
                "HTTP_X_FORWARDED_PREFIX" in environ
                or "HTTP_X_FORWARDED_FOR" in environ
            ):
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

    # ── Logging ────────────────────────────────────────────────────────────────
    _log_dir = Path(__file__).resolve().parent.parent / "logs"
    _log_formatter = logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    _console_handler = logging.StreamHandler()
    _console_handler.setFormatter(_log_formatter)
    _console_handler.setLevel(logging.DEBUG)

    # Only add file handlers when the logs directory is writable (not in Docker)
    _use_file_log = False
    try:
        _log_dir.mkdir(exist_ok=True)
        (_log_dir / ".write_test").touch()
        (_log_dir / ".write_test").unlink()
        _use_file_log = True
    except OSError:
        pass

    _indexing_logger = logging.getLogger("indexing")
    _indexing_logger.setLevel(logging.DEBUG)
    _indexing_logger.addHandler(_console_handler)
    if _use_file_log:
        _file_handler = logging.handlers.RotatingFileHandler(
            _log_dir / "indexing.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        _file_handler.setFormatter(_log_formatter)
        _file_handler.setLevel(logging.DEBUG)
        _indexing_logger.addHandler(_file_handler)
    _indexing_logger.propagate = False

    app.logger.setLevel(logging.DEBUG)
    app.logger.addHandler(_console_handler)
    if _use_file_log:
        _chat_file_handler = logging.handlers.RotatingFileHandler(
            _log_dir / "chat.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        _chat_file_handler.setFormatter(_log_formatter)
        _chat_file_handler.setLevel(logging.DEBUG)
        app.logger.addHandler(_chat_file_handler)

    # ── Services ───────────────────────────────────────────────────────────────
    _project_root = Path(__file__).resolve().parent.parent

    services.solr = pysolr.Solr(
        cfg.SOLR_URL, timeout=cfg.SOLR_TIMEOUT, always_commit=False
    )
    services.indexing_store = IndexingStateStore(cfg.DB_URL)

    # Chat history store (same SQLite DB, separate tables)
    from web.chat_store import ChatStore

    services.chat_store = ChatStore(cfg.DB_URL)

    _embedder = Embedder(
        model_name=cfg.SEMANTIC_MODEL,
        base_url=cfg.EMBEDDER_URL,
        batch_size=cfg.EMBED_BATCH_SIZE,
        timeout=cfg.EMBED_TIMEOUT,
    )
    try:
        _embedder.embed(["warmup"])
        logging.getLogger(__name__).info("Embedder warmup OK (%s)", cfg.SEMANTIC_MODEL)
    except Exception as _exc:
        logging.getLogger(__name__).warning("Embedder warmup failed (non-fatal): %s", _exc)
    _qdrant_store = QdrantStore(
        cfg.QDRANT_URL,
        collection_name=cfg.QDRANT_COLLECTION,
        embedder=_embedder,
    )
    services.ingest_service = _qdrant_store    # backward compat
    services.retrieval_service = _qdrant_store  # backward compat
    services.solr_store = SolrStore(services.solr)

    # Knowledge service (manual nodes/edges/texts CRUD)
    from knowledge.service import KnowledgeService
    from retrieval.graph_service import GraphService

    _graph_client = None
    if _GRAPH_IMPORTS_OK and cfg.GRAPH_ENABLED:
        try:
            # init_client() is idempotent (singleton); calling it here ensures
            # GraphService and KnowledgeService get a live client.
            _graph_client = _graph_init_client(
                cfg.NEO4J_URL, cfg.NEO4J_USER, cfg.NEO4J_PASSWORD, cfg.NEO4J_DATABASE
            )
            if not _graph_client.available:
                _graph_client = None
        except Exception as exc:
            logging.getLogger(__name__).warning("Graph client init failed: %s", exc)
    services.graph_service = GraphService(_graph_client)
    services.knowledge_service = KnowledgeService(
        services.indexing_store,
        _graph_client,
        services.solr_store,
        services.ingest_service,
    )

    services.indexing_worker = IndexingWorker(
        services.indexing_store,
        services.solr_store,
        semantic_service=services.ingest_service,
        on_change=lambda: _notify_clients(),
        knowledge_service=services.knowledge_service,
    )
    services.socketio = socketio

    # ── Blueprints & filters ───────────────────────────────────────────────────
    register_filters(app)
    app.register_blueprint(search_bp)
    app.register_blueprint(ingest_bp)
    app.register_blueprint(retrieval_bp)
    app.register_blueprint(knowledge_bp)

    # ── Import and register agent routes if available ──────────────────────────
    try:
        from web.agent_routes import agent_bp

        app.register_blueprint(agent_bp)
    except ImportError:
        pass

    # ── Before-request hook ────────────────────────────────────────────────────
    @app.before_request
    def ensure_indexing_worker() -> None:
        if not services.indexing_worker.is_running:
            services.indexing_worker.start()

    # ── Socket events ──────────────────────────────────────────────────────────
    @socketio.on("connect")
    def _on_socket_connect():
        socketio.emit("indexing_status", _build_status_payload())

    return app, socketio
