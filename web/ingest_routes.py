"""Blueprint: ingest routes (/indexing, /api/indexing/*)."""

from __future__ import annotations

import logging
import tempfile
import threading
from pathlib import Path

from flask import Blueprint, jsonify, render_template, request

import config as cfg
import services

logger = logging.getLogger(__name__)
from ingest.models import IndexJobConfig
from ingest.readers.factory import ReaderFactory
from ingest.run_all import (
    _build_status_payload,
    _notify_clients,
    _run_all_lock,
    _run_all_state,
    _run_all_worker,
)
from ingest.source_manager import (
    _load_sources_file,
    _normalise_source,
    _save_user_sources,
    get_all_index_sources,
    user_index_sources_file,
)
from ingest.chunkers.factory import chunk_for_semantic, get_chunker

# ── Graph extraction (optional) ────────────────────────────────────────────────
try:
    from graph.db.entities import ExtractionContext
    from graph.pipeline.registry import ExtractorRegistry as _ExtractorRegistry

    _DEBUG_REGISTRY: "_ExtractorRegistry | None" = None

    def _get_debug_registry() -> "_ExtractorRegistry":
        global _DEBUG_REGISTRY
        if _DEBUG_REGISTRY is None:
            reg = _ExtractorRegistry().load_defaults()
            rules_path = Path(cfg.GRAPH_RULES_PATH) if cfg.GRAPH_RULES_PATH else None
            if rules_path and rules_path.exists():
                reg.load_rules(str(rules_path))
            _DEBUG_REGISTRY = reg
        return _DEBUG_REGISTRY

    _GRAPH_IMPORTS_OK = True
except Exception:
    _GRAPH_IMPORTS_OK = False


def _run_extraction_debug(file_path_str: str, content_text: str) -> dict:
    """Run pipeline extractors on content and return serialisable result.

    Uses a dummy ExtractionContext (repository="debug") since we don't have
    real repo metadata in the debug flow.
    """
    if not _GRAPH_IMPORTS_OK:
        return {"available": False, "reason": "graph imports unavailable"}
    try:
        registry = _get_debug_registry()
        ctx = ExtractionContext(repository="debug", repository_path="debug")
        extractors = registry.extractors_for(file_path_str, content_text)
        if not extractors:
            return {"available": True, "extractor_names": [], "nodes": [], "edges": []}

        all_nodes: list[dict] = []
        all_edges: list[dict] = []
        extractor_names: list[str] = []
        for extractor in extractors:
            name = type(extractor).__name__
            extractor_names.append(name)
            try:
                result = extractor.extract(file_path_str, content_text, ctx)
                for node in result.nodes:
                    all_nodes.append({
                        "extractor": name,
                        "label": node.label,
                        "key": node.key,
                        "key_value": node.key_value,
                        "properties": node.properties,
                    })
                for edge in result.edges:
                    all_edges.append({
                        "extractor": name,
                        "from_label": edge.from_label,
                        "from_key_value": edge.from_key_value,
                        "rel_type": edge.rel_type,
                        "to_label": edge.to_label,
                        "to_key_value": edge.to_key_value,
                        "properties": edge.properties,
                    })
            except Exception as exc:
                logger.warning("Extractor %s failed in debug mode: %s", name, exc)
        return {
            "available": True,
            "extractor_names": extractor_names,
            "nodes": all_nodes,
            "edges": all_edges,
        }
    except Exception as exc:
        logger.warning("_run_extraction_debug failed: %s", exc)
        return {"available": True, "extractor_names": [], "nodes": [], "edges": [], "error": str(exc)}


ingest_bp = Blueprint("ingest", __name__)


@ingest_bp.route("/indexing")
def indexing_page():
    prefix = "/" + cfg.APP_PREFIX.strip("/") if cfg.APP_PREFIX else ""
    socket_io_path = prefix + "/socket.io"
    return render_template("ingest.html", socket_io_path=socket_io_path)


# ── Source management ──────────────────────────────────────────────────────────


@ingest_bp.route("/api/indexing/sources")
def api_indexing_sources():
    return jsonify({"ok": True, "sources": get_all_index_sources()})


@ingest_bp.route("/api/indexing/sources", methods=["POST"])
def api_indexing_sources_add():
    body = request.get_json(silent=True) or {}
    source = _normalise_source(body)

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


# ── Indexing control ───────────────────────────────────────────────────────────


@ingest_bp.route("/api/indexing/start", methods=["POST"])
def api_indexing_start():
    body = request.get_json(silent=True) or {}

    root_path = str(body.get("root_path", "")).strip()
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

    repository_path = repo_name
    repository = repo_name

    existing = services.indexing_store.get_pending_job_for_source(
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
    job_id = services.indexing_store.create_job(
        IndexJobConfig(
            root_path=root.as_posix(),
            repository=repository,
            repository_path=repository_path,
            repository_url_base=repository_url_base,
            extensions=extensions,
            index_mode=index_mode,
        )
    )
    services.indexing_worker.start()
    return jsonify({"ok": True, "job_id": job_id})


@ingest_bp.route("/api/indexing/run-all", methods=["POST"])
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


@ingest_bp.route("/api/indexing/stop", methods=["POST"])
def api_indexing_stop():
    active = services.indexing_store.get_active_job()
    if not active:
        return jsonify({"ok": False, "error": "No active indexing job"}), 404

    services.indexing_store.request_cancel(int(active["id"]))
    return jsonify(
        {"ok": True, "job_id": int(active["id"]), "message": "Cancel requested"}
    )


@ingest_bp.route("/api/indexing/status")
def api_indexing_status():
    return jsonify(_build_status_payload())


# ── Data clearing ──────────────────────────────────────────────────────────────


@ingest_bp.route("/api/indexing/clear/neo4j", methods=["POST"])
def api_clear_neo4j():
    try:
        if not services.graph_service or not services.graph_service.available:
            return jsonify({"ok": False, "error": "Graph client unavailable"}), 503
        services.graph_service.clear()
        return jsonify({"ok": True, "message": "Neo4j graph cleared"})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@ingest_bp.route("/api/indexing/clear/solr", methods=["POST"])
def api_clear_solr():
    try:
        services.solr.delete(q="*:*")
        services.solr.commit()
        return jsonify({"ok": True, "message": "Solr index cleared"})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@ingest_bp.route("/api/indexing/clear/qdrant", methods=["POST"])
def api_clear_qdrant():
    if services.ingest_service is None:
        return jsonify({"ok": False, "error": "Semantic service unavailable"}), 503
    try:
        services.ingest_service.clear_collection()
        return jsonify({"ok": True, "message": "Qdrant collection cleared"})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@ingest_bp.route("/api/indexing/clear/sqlite", methods=["POST"])
def api_clear_sqlite():
    try:
        services.indexing_store.clear_all_data()
        _notify_clients()
        return jsonify({"ok": True, "message": "SQLite history cleared"})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@ingest_bp.route("/api/indexing/clear/all", methods=["POST"])
def api_clear_all():
    errors: list[str] = []

    try:
        services.solr.delete(q="*:*")
        services.solr.commit()
    except Exception as exc:
        errors.append(f"Solr: {exc}")

    if services.ingest_service is not None:
        try:
            services.ingest_service.clear_collection()
        except Exception as exc:
            errors.append(f"Qdrant: {exc}")

    try:
        services.indexing_store.clear_all_data()
        _notify_clients()
    except Exception as exc:
        errors.append(f"SQLite: {exc}")

    try:
        if services.graph_service and services.graph_service.available:
            services.graph_service.clear()
    except Exception as exc:
        errors.append(f"Neo4j: {exc}")

    if errors:
        return jsonify({"ok": False, "errors": errors}), 500
    return jsonify({"ok": True, "message": "All data cleared (Solr + Qdrant + SQLite + Neo4j)"})


@ingest_bp.route("/api/indexing/debug-parse-path", methods=["POST"])
def api_indexing_debug_parse_path():
    """Parse an already-indexed file by its absolute path on disk.

    Security gate: the path must exist in the indexing store (i.e. it was
    previously submitted for indexing by the user).
    """
    body = request.get_json(silent=True) or {}
    file_path = (body.get("file_path") or "").strip()
    if not file_path:
        return jsonify({"ok": False, "error": "file_path required"}), 400

    # Security: only allow files that are already in the indexing store
    known = services.indexing_store.get_indexed_file(file_path)
    if known is None:
        return jsonify({"ok": False, "error": "File not in index store"}), 403

    source = Path(file_path)
    if not source.exists():
        return jsonify({"ok": False, "error": "File not found on disk"}), 404

    try:
        reader_factory = ReaderFactory()
        reader = reader_factory.get_reader(source)
        reader_name = type(reader).__name__
        logger.info("debug-parse-path: %s  reader=%s", source.name, reader_name)

        try:
            content_text = reader.read_content(source)
            logger.debug("  read_content: %d chars", len(content_text))
        except Exception as exc:
            logger.warning("  read_content error: %s", exc)
            content_text = f"[read_content error] {exc}"

        try:
            semantic_text = reader.read_semantic(source)
            logger.debug("  read_semantic: %d chars", len(semantic_text) if semantic_text else 0)
        except Exception as exc:
            logger.warning("  read_semantic error: %s", exc)
            semantic_text = f"[read_semantic error] {exc}"

        chunker_name = type(get_chunker(source.name)).__name__
        try:
            chunks_raw = chunk_for_semantic(source.name, semantic_text or content_text)
            logger.info("  chunks: %d  chunker=%s", len(chunks_raw), chunker_name)
            chunks = [
                {
                    "index": i,
                    "chunk_id": c.chunk_id,
                    "chunk_type": c.chunk_type,
                    "chars": len(c.text),
                    "text": c.text,
                }
                for i, c in enumerate(chunks_raw)
            ]
        except Exception as exc:
            chunks = [
                {
                    "index": 0,
                    "chunk_id": "error",
                    "chunk_type": "error",
                    "chars": 0,
                    "text": str(exc),
                }
            ]

        extraction = _run_extraction_debug(str(source), content_text)

        return jsonify(
            {
                "ok": True,
                "file_name": source.name,
                "reader_name": reader_name,
                "chunker_name": chunker_name,
                "content_chars": len(content_text),
                "content_text": content_text,
                "semantic_chars": len(semantic_text) if semantic_text else 0,
                "semantic_text": semantic_text or "",
                "chunk_count": len(chunks),
                "chunks": chunks,
                "extraction": extraction,
            }
        )
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@ingest_bp.route("/api/indexing/debug-parse", methods=["POST"])
def api_indexing_debug_parse():
    """Upload a file and return the reader text + semantic chunks for debugging."""
    tmp_path = None
    try:
        if "file" not in request.files:
            return jsonify({"ok": False, "error": "No file uploaded"}), 400
        f = request.files["file"]
        if not f.filename:
            return jsonify({"ok": False, "error": "Empty filename"}), 400

        data = f.read()
        if len(data) > 20 * 1024 * 1024:
            return jsonify({"ok": False, "error": "File too large (max 20 MB)"}), 400

        original_name = Path(f.filename).name
        suffix = Path(original_name).suffix or ".bin"

        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(data)
            tmp_path = Path(tmp.name)

        reader_factory = ReaderFactory()
        reader = reader_factory.get_reader(tmp_path)
        reader_name = type(reader).__name__

        try:
            content_text = reader.read_content(tmp_path)
        except Exception as exc:
            content_text = f"[read_content error] {exc}"

        try:
            semantic_text = reader.read_semantic(tmp_path)
        except Exception as exc:
            semantic_text = f"[read_semantic error] {exc}"

        chunker_name = type(get_chunker(original_name)).__name__
        try:
            chunks_raw = chunk_for_semantic(original_name, semantic_text)
            chunks = [
                {
                    "index": i,
                    "chunk_id": c.chunk_id,
                    "chunk_type": c.chunk_type,
                    "chars": len(c.text),
                    "text": c.text,
                }
                for i, c in enumerate(chunks_raw)
            ]
        except Exception as exc:
            chunks = [
                {
                    "index": 0,
                    "chunk_id": "error",
                    "chunk_type": "error",
                    "chars": 0,
                    "text": str(exc),
                }
            ]

        extraction = _run_extraction_debug(original_name, content_text)

        return jsonify(
            {
                "ok": True,
                "file_name": original_name,
                "reader_name": reader_name,
                "chunker_name": chunker_name,
                "content_chars": len(content_text),
                "content_text": content_text,
                "semantic_chars": len(semantic_text),
                "semantic_text": semantic_text,
                "chunk_count": len(chunks),
                "chunks": chunks,
                "extraction": extraction,
            }
        )
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    finally:
        if tmp_path and tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception as exc:
                logger.warning("Could not delete temp file %s: %s", tmp_path, exc)
