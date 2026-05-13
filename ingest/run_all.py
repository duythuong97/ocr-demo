"""Run-all worker: VCS update + queue all sources for indexing."""
from __future__ import annotations

import logging
import subprocess
import threading
from pathlib import Path

import services
from ingest.models import IndexJobConfig
from ingest.source_manager import get_all_index_sources

_indexing_logger = logging.getLogger("indexing")

_run_all_lock = threading.Lock()
_run_all_state: dict = {
    "running": False,
    "total": 0,
    "done": 0,
    "log": [],
}


# ── Status helpers (used by socket events and indexing routes) ─────────────────

def _build_status_payload() -> dict:
    with _run_all_lock:
        run_all = dict(_run_all_state)
    return {
        "ok": True,
        "worker_running": services.indexing_worker.is_running if services.indexing_worker else False,
        "active_job": dict(services.indexing_store.get_active_job()) if services.indexing_store and services.indexing_store.get_active_job() else None,
        "recent_jobs": services.indexing_store.list_recent_jobs(limit=12) if services.indexing_store else [],
        "recent_files": services.indexing_store.list_recent_indexed_files(limit=40) if services.indexing_store else [],
        "run_all": run_all,
    }


def _notify_clients() -> None:
    try:
        if services.socketio is not None:
            services.socketio.emit("indexing_status", _build_status_payload(), namespace="/")
    except Exception:
        _indexing_logger.warning("Socket emit failed", exc_info=True)


# ── VCS helpers ────────────────────────────────────────────────────────────────

def _vcs_update(vcs_path: Path, name: str, log_fn) -> None:
    """Run git pull or svn update on vcs_path. Logs results via log_fn."""
    if (vcs_path / ".git").exists():
        log_fn(f"[{name}] git pull {vcs_path}")
        try:
            result = subprocess.run(
                ["git", "-C", str(vcs_path), "pull"],
                capture_output=True,
                text=True,
                timeout=180,
            )
            msg = result.stdout.strip() or result.stderr.strip() or "(no output)"
            status = "OK" if result.returncode == 0 else "FAILED"
            for line in msg.splitlines():
                log_fn(f"[{name}] git {status}: {line}")
        except subprocess.TimeoutExpired:
            log_fn(f"[{name}] git pull timed out")
        except FileNotFoundError:
            log_fn(f"[{name}] git not found in PATH")
    elif (vcs_path / ".svn").exists():
        log_fn(f"[{name}] svn update {vcs_path}")
        try:
            result = subprocess.run(
                ["svn", "update", str(vcs_path)],
                capture_output=True,
                text=True,
                timeout=180,
            )
            msg = result.stdout.strip() or result.stderr.strip() or "(no output)"
            status = "OK" if result.returncode == 0 else "FAILED"
            for line in msg.splitlines():
                log_fn(f"[{name}] svn {status}: {line}")
        except subprocess.TimeoutExpired:
            log_fn(f"[{name}] svn update timed out")
        except FileNotFoundError:
            log_fn(f"[{name}] svn not found in PATH")
    else:
        log_fn(f"[{name}] No VCS detected, skipping update")


# ── Main worker ────────────────────────────────────────────────────────────────

def _run_all_worker() -> None:
    sources = get_all_index_sources()

    def _log(msg: str) -> None:
        _indexing_logger.info("run-all: %s", msg)
        with _run_all_lock:
            _run_all_state["log"].append(msg)
            _run_all_state["log"] = _run_all_state["log"][-100:]
        _notify_clients()

    with _run_all_lock:
        _run_all_state.update(
            {"running": True, "total": len(sources), "done": 0, "log": []}
        )

    _log(f"Starting run-all for {len(sources)} sources")

    for src in sources:
        root = Path(src["root_path"])
        repo_sub = src.get("repository_path", "").strip()
        vcs_path = root / repo_sub if repo_sub else root
        name = src.get("name") or src.get("repository") or str(root)

        try:
            _vcs_update(vcs_path, name, _log)
        except Exception as exc:
            _log(f"[{name}] VCS update error: {exc}")

        try:
            existing = services.indexing_store.get_pending_job_for_source(
                src["root_path"], src.get("repository_path", "")
            )
            if existing:
                _log(
                    f"[{name}] Already queued as job #{existing['id']} ({existing['status']}), skipping"
                )
            else:
                src_mode = src.get("index_mode", "both")
                if src_mode not in ("both", "solr", "semantic"):
                    src_mode = "both"
                job_id = services.indexing_store.create_job(
                    IndexJobConfig(
                        root_path=src["root_path"],
                        repository=src.get("repository", ""),
                        repository_path=src.get("repository_path", ""),
                        repository_url_base=src.get("repository_url_base", ""),
                        extensions=src.get("extensions", []),
                        index_mode=src_mode,
                    )
                )
                _log(f"[{name}] Queued as job #{job_id}")
        except Exception as exc:
            _log(f"[{name}] Failed to queue: {exc}")

        with _run_all_lock:
            _run_all_state["done"] += 1

    services.indexing_worker.start()
    _log("All sources queued — worker started")

    with _run_all_lock:
        _run_all_state["running"] = False
    _notify_clients()
