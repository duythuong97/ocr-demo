"""Jinja2 template filters and globals for the Flask app."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
from urllib.parse import quote

import config as cfg


def register_filters(app) -> None:
    """Register all custom Jinja2 filters and globals on *app*."""

    @app.template_filter("str_val")
    def str_val_filter(value) -> str:
        """Safely coerce a Solr field to str — extracts first element if it's a list."""
        if isinstance(value, list):
            return value[0] if value else ""
        return value or ""

    @app.template_filter("basename")
    def basename_filter(value) -> str:
        """Return the final component of a file path, handling both / and \\ separators."""
        if isinstance(value, list):
            value = value[0] if value else ""
        if not value:
            return value
        from posixpath import basename as posix_basename
        from ntpath import basename as nt_basename

        return nt_basename(value) if "\\" in value else posix_basename(value)

    @app.template_filter("format_number")
    def fmt_number(value):
        """Format an integer with thousands separators."""
        try:
            return f"{int(value):,}"
        except (ValueError, TypeError):
            return value

    @app.template_filter("remove_param")
    def remove_param(url: str, param: str) -> str:
        """Return the URL with the specified query param removed."""
        parsed = urlparse(url)
        qs = parse_qs(parsed.query, keep_blank_values=True)
        qs.pop(param, None)
        new_query = urlencode(qs, doseq=True)
        return urlunparse(parsed._replace(query=new_query))

    @app.template_filter("set_param")
    def set_param(url: str, param: str, value: str) -> str:
        """Return the URL with the specified query param set to a new value."""
        parsed = urlparse(url)
        qs = parse_qs(parsed.query, keep_blank_values=True)
        qs[param] = [value]
        new_query = urlencode(qs, doseq=True)
        return urlunparse(parsed._replace(query=new_query))

    @app.template_filter("add_facet")
    def add_facet(url: str, field: str, value: str) -> str:
        """Add or update a facet parameter and reset page to 1."""
        parsed = urlparse(url)
        qs = parse_qs(parsed.query, keep_blank_values=True)
        qs[field] = [value]
        qs["page"] = ["1"]
        new_query = urlencode(qs, doseq=True)
        return urlunparse(parsed._replace(query=new_query))

    def _facet_param_name(field: str) -> str:
        facet_param_map = {
            cfg.FIELD_REPOSITORY: "repository",
            cfg.FIELD_FILE_TYPE: "file_type",
        }
        return facet_param_map.get(field, field)

    @app.template_filter("toggle_facet")
    def toggle_facet(url: str, field: str, value: str) -> str:
        """Toggle a facet value on/off (multi-select) and reset page to 1."""
        parsed = urlparse(url)
        qs = parse_qs(parsed.query, keep_blank_values=True)
        param = _facet_param_name(field)

        current = qs.get(param, [])
        if value in current:
            current = [v for v in current if v != value]
        else:
            current = current + [value]

        if current:
            qs[param] = current
        else:
            qs.pop(param, None)

        qs["page"] = ["1"]
        new_query = urlencode(qs, doseq=True)
        return urlunparse(parsed._replace(query=new_query))

    # Project root — one level above web/
    _project_root = Path(__file__).resolve().parent.parent

    @app.template_global("local_file_href")
    def local_file_href(doc: dict) -> str | None:
        """Build a local file:// link for a search result when the file exists."""
        file_path = doc.get(cfg.FIELD_FILE_PATH) or doc.get("file_path")
        if not file_path:
            return None

        path = Path(file_path)
        if not path.is_absolute():
            local_root = Path(
                getattr(cfg, "LOCAL_FILE_ROOT", _project_root)
            )
            path = local_root / file_path

        if not path.exists():
            return None

        return path.resolve().as_uri()

    @app.template_global("repository_href")
    def repository_href(doc: dict) -> str | None:
        """Build a repository browser URL for a search result."""
        direct_url = doc.get(cfg.FIELD_URL) or doc.get("url")
        if direct_url:
            return direct_url

        file_path = doc.get(cfg.FIELD_FILE_PATH) or doc.get("file_path")
        repo_path = doc.get(cfg.FIELD_REPOSITORY_PATH) or doc.get("repository_path")
        if repo_path and file_path:
            fp = Path(file_path)
            if fp.is_absolute():
                return repo_path
            rel = fp.as_posix()
            return f"{repo_path.rstrip('/')}/{quote(rel, safe='/:@')}"
        if repo_path:
            return repo_path
