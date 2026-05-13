"""Source management: load/save/normalise index source configs."""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Canonical paths — relative to the project root (one level above this file).
_project_root = Path(__file__).resolve().parent.parent
default_index_sources_file = _project_root / "indexing" / "index_sources.json"
user_index_sources_file = _project_root / "data" / "index_sources.user.json"


def _normalise_extensions(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip().lstrip(".") for v in value if str(v).strip()]
    if isinstance(value, str):
        return [v.strip().lstrip(".") for v in value.split(",") if v.strip()]
    return []


def _normalise_source(src: dict, default_flag: bool = False) -> dict:
    root_path = str(src.get("root_path", "")).strip().replace("\\", "/")
    return {
        "name": str(src.get("name", "")).strip(),
        "root_path": root_path,
        "repository": str(src.get("repository", "")).strip(),
        "repository_path": str(src.get("repository_path", ""))
        .strip()
        .replace("\\", "/"),
        "repository_url_base": str(src.get("repository_url_base", "")).strip(),
        "extensions": _normalise_extensions(src.get("extensions", [])),
        "is_default": bool(src.get("is_default", default_flag)),
    }


def _load_sources_file(path: Path, default_flag: bool = False) -> list[dict]:
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            return []
        out = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            item_norm = _normalise_source(item, default_flag=default_flag)
            if not item_norm["root_path"]:
                continue
            out.append(item_norm)
        return out
    except Exception as exc:
        logger.error("Failed loading source config from %s: %s", path, exc)
        return []


def _save_user_sources(items: list[dict]) -> None:
    user_index_sources_file.parent.mkdir(parents=True, exist_ok=True)
    payload = [
        {
            "name": i["name"],
            "root_path": i["root_path"],
            "repository": i["repository"],
            "repository_path": i["repository_path"],
            "repository_url_base": i["repository_url_base"],
            "extensions": i["extensions"],
        }
        for i in items
    ]
    user_index_sources_file.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )


def get_all_index_sources() -> list[dict]:
    default_items = _load_sources_file(default_index_sources_file, default_flag=True)
    user_items = _load_sources_file(user_index_sources_file, default_flag=False)
    return default_items + user_items
