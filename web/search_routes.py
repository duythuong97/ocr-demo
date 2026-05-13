"""Blueprint: search routes (/, /search, /api/search, /api/open-file)."""
from __future__ import annotations

import platform
import subprocess
from pathlib import Path

from flask import Blueprint, jsonify, redirect, render_template, request, url_for

import config as cfg
from search import execute_search
from search.params import parse_form

search_bp = Blueprint("search", __name__)

# Project root (one level above web/)
_project_root = Path(__file__).resolve().parent.parent


@search_bp.route("/")
def index():
    p = parse_form(request.args)
    has_input = bool(
        p["query"]
        or p["repository"]
        or p["file_type"]
        or p["date_from"]
        or p["date_to"]
        or p["extra_fq"]
    )
    data = execute_search(p) if has_input else None
    return render_template(
        "index.html",
        file_types=cfg.FILE_TYPES,
        sort_options=cfg.SORT_OPTIONS,
        default_rows=cfg.DEFAULT_ROWS,
        search=data,
        params=p,
    )


@search_bp.route("/search")
def search():
    p = parse_form(request.args)
    if not p["query"]:
        return redirect(url_for("search.index"))
    data = execute_search(p)
    return render_template(
        "index.html",
        file_types=cfg.FILE_TYPES,
        sort_options=cfg.SORT_OPTIONS,
        default_rows=cfg.DEFAULT_ROWS,
        search=data,
        params=p,
    )


@search_bp.route("/api/search")
def api_search():
    """JSON API — called by the frontend via fetch."""
    p = parse_form(request.args)
    data = execute_search(p)
    data["docs"] = [
        {
            k: (v[0] if isinstance(v, list) and len(v) == 1 else v)
            for k, v in doc.items()
        }
        for doc in data["docs"]
    ]
    return jsonify(data)


@search_bp.route("/api/open-file")
def api_open_file():
    """Open a local file/folder in the OS file manager (Explorer / Finder).
    Only resolves paths under LOCAL_FILE_ROOT for safety.
    """
    file_path = request.args.get("path", "").strip()
    if not file_path:
        return jsonify({"error": "No path provided"}), 400

    local_root = Path(getattr(cfg, "LOCAL_FILE_ROOT", _project_root))
    path = Path(file_path)
    if not path.is_absolute():
        path = local_root / file_path

    try:
        path = path.resolve()
        path.relative_to(local_root.resolve())
    except ValueError:
        return jsonify({"error": "Path outside allowed root"}), 403

    if not path.exists():
        return jsonify({"error": "File not found"}), 404

    system = platform.system()
    try:
        if system == "Windows":
            subprocess.Popen(["explorer", "/select,", str(path)])
        elif system == "Darwin":
            subprocess.Popen(["open", "-R", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path.parent)])
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    return jsonify({"ok": True, "path": str(path)})
