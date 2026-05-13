"""AppSettingsExtractor — parse .NET appsettings.json and service-config.json files.

Handles:
  - appsettings.json, appsettings.{Environment}.json
  - service-config.json, service-config.{Environment}.json

Extracts:
  - ConnectionStrings → ExternalService node per connection, Service CALLS_EXTERNAL it
  - Any URL-valued keys (BaseUrl, ApiUrl, Endpoint, …) → ExternalService + CALLS_EXTERNAL
  - Serilog/NLog configuration (logging) → noted but not graphed

Node types: Service (current app), ExternalService (DBs, APIs)
Relationships: CALLS_EXTERNAL
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from graph.extractors.base import BaseExtractor
from graph.db.entities import ExtractionContext, ExtractionResult, GraphEdge, GraphNode
from graph.db import schema as S

logger = logging.getLogger(__name__)

_HANDLED_NAMES = re.compile(
    r'^(?:appsettings|service-config)(?:\.[^.]+)?\.json$',
    re.IGNORECASE,
)

# Keys whose values are URLs pointing to external services
_URL_KEY_PATTERN = re.compile(
    r'(?:url|uri|endpoint|baseurl|baseaddress|host|address|server|connection)s?$',
    re.IGNORECASE,
)

# Detect http/https URLs
_URL_VALUE = re.compile(r'^https?://', re.IGNORECASE)

# Skip non-meaningful / internal-only config keys
_SKIP_SECTIONS = {"Serilog", "NLog", "Logging", "AllowedHosts"}


class AppSettingsExtractor(BaseExtractor):
    def can_handle(self, file_path: str, text: str) -> bool:
        return bool(_HANDLED_NAMES.match(Path(file_path).name))

    def extract(self, file_path: str, text: str, context: ExtractionContext) -> ExtractionResult:
        result = ExtractionResult(source_file=file_path, extractor_name="AppSettingsExtractor")

        try:
            data = json.loads(text)
        except Exception:
            return result

        if not isinstance(data, dict):
            return result

        # Service node derived from repository or parent folder name
        service_name = context.service_name or context.repository or Path(file_path).parent.name
        service_qname = f"Service:{service_name}"
        result.nodes.append(GraphNode(
            label=S.LABEL_SERVICE,
            key="qualified_name",
            key_value=service_qname,
            properties={
                "name": service_name,
                "repository": context.repository,
                "source_file": file_path,
            },
        ))

        # Extract ConnectionStrings section
        conn_strings = data.get("ConnectionStrings") or data.get("connectionStrings") or {}
        if isinstance(conn_strings, dict):
            for conn_name, conn_value in conn_strings.items():
                if not conn_name or not isinstance(conn_value, str):
                    continue
                ext_qname = f"ExternalService:db:{conn_name.lower()}"
                # Try to parse a meaningful DB name from the connection string
                db_name = _parse_db_name(conn_value) or conn_name
                result.nodes.append(GraphNode(
                    label=S.LABEL_EXTERNAL_SERVICE,
                    key="qualified_name",
                    key_value=ext_qname,
                    properties={
                        "name": conn_name,
                        "kind": "database",
                        "db_name": db_name,
                        "source_file": file_path,
                    },
                ))
                result.edges.append(GraphEdge(
                    from_label=S.LABEL_SERVICE,
                    from_key="qualified_name",
                    from_key_value=service_qname,
                    to_label=S.LABEL_EXTERNAL_SERVICE,
                    to_key="qualified_name",
                    to_key_value=ext_qname,
                    rel_type=S.REL_CALLS_EXTERNAL,
                    properties={"connection_name": conn_name},
                ))

        # Recursively walk the JSON looking for URL-valued keys (skip known log sections)
        _walk_for_urls(data, file_path, service_qname, result, skip_keys=_SKIP_SECTIONS)

        return result


def _parse_db_name(conn_str: str) -> str | None:
    """Try to extract a DB/data source name from a connection string."""
    for pattern in (
        re.compile(r'(?:Database|Initial Catalog|database)=([^;]+)', re.IGNORECASE),
        re.compile(r'Data Source=([^;]+)', re.IGNORECASE),
    ):
        m = pattern.search(conn_str)
        if m:
            return m.group(1).strip()
    return None


def _walk_for_urls(
    obj: Any,
    file_path: str,
    service_qname: str,
    result: ExtractionResult,
    skip_keys: set[str],
    _depth: int = 0,
) -> None:
    """Recursively look for URL-valued string keys."""
    if _depth > 6 or not isinstance(obj, dict):
        return
    for key, value in obj.items():
        if key in skip_keys:
            continue
        if isinstance(value, str) and _URL_VALUE.match(value) and _URL_KEY_PATTERN.search(key):
            ext_qname = f"ExternalService:url:{value.rstrip('/').lower()}"
            result.nodes.append(GraphNode(
                label=S.LABEL_EXTERNAL_SERVICE,
                key="qualified_name",
                key_value=ext_qname,
                properties={
                    "name": key,
                    "url": value,
                    "kind": "http_service",
                    "source_file": file_path,
                },
            ))
            result.edges.append(GraphEdge(
                from_label=S.LABEL_SERVICE,
                from_key="qualified_name",
                from_key_value=service_qname,
                to_label=S.LABEL_EXTERNAL_SERVICE,
                to_key="qualified_name",
                to_key_value=ext_qname,
                rel_type=S.REL_CALLS_EXTERNAL,
                properties={"config_key": key},
            ))
        elif isinstance(value, dict):
            _walk_for_urls(value, file_path, service_qname, result, skip_keys, _depth + 1)
