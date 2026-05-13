"""
Flask Solr Search Application — entry point.

All application logic lives in:
  web/          Flask app factory, blueprints, Jinja2 filters
  search/       Full-text, semantic and hybrid search logic
  chat/         RAG streaming
  indexing/     Indexing worker, source management, run-all
  services.py   Module-level service singletons
"""

from __future__ import annotations
import logging
import os
import signal
import threading

import config as cfg
import services
from web import create_app

app, socketio = create_app()

_logger = logging.getLogger(__name__)




_shutdown_lock = threading.Lock()
_shutdown_signal_count = 0


def _force_exit_later(delay_seconds: int) -> None:
    def _runner():
        try:
            threading.Event().wait(max(1, delay_seconds))
        finally:
            os._exit(130)

    t = threading.Thread(target=_runner, daemon=True, name="force-exit")
    t.start()


def _handle_shutdown_signal(signum, frame) -> None:
    global _shutdown_signal_count
    with _shutdown_lock:
        _shutdown_signal_count += 1
        count = _shutdown_signal_count

    if count >= 2:
        _logger.warning("Received repeated shutdown signal; forcing exit now.")
        os._exit(130)

    _logger.info("Shutdown signal received (%s). Stopping worker...", signum)
    try:
        active = services.indexing_store.get_active_job()
        if active:
            services.indexing_store.request_cancel(int(active["id"]))
    except Exception as exc:
        _logger.warning("Could not cancel active job during shutdown: %s", exc)

    try:
        services.indexing_worker.stop()
    except Exception as exc:
        _logger.warning("Could not stop indexing worker during shutdown: %s", exc)

    timeout_seconds = int(getattr(cfg, "SHUTDOWN_FORCE_EXIT_SECONDS", 8) or 8)
    _logger.info(
        "Graceful shutdown started; process will be force-killed in %ds if still alive.",
        timeout_seconds,
    )
    _force_exit_later(timeout_seconds)


if __name__ == "__main__":
    signal.signal(signal.SIGINT, _handle_shutdown_signal)
    signal.signal(signal.SIGTERM, _handle_shutdown_signal)
    socketio.run(
        app,
        host="0.0.0.0",
        debug=True,
        port=cfg.FLASK_PORT,
        allow_unsafe_werkzeug=True,
        use_reloader=False,
    )
