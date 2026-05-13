"""WebConfigExtractor — parse .NET Framework web.config and app.config files.

.NET Framework uses XML-based configuration instead of appsettings.json.
This extractor handles:
  - web.config (ASP.NET MVC/WebForms/WebAPI)
  - app.config (WinForms, Console, WCF services)
  - *.exe.config (deployed app config)

Extracts:
  - <connectionStrings><add name="..." connectionString="..."/> → ExternalService (database)
  - <appSettings><add key="*Url*|*Endpoint*|*Host*" value="https://..."/> → ExternalService (http)
  - <client><endpoint address="https://..."> (WCF) → ExternalService

Node types: Service (current app), ExternalService (DBs, APIs, WCF services)
Relationships: CALLS_EXTERNAL
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from graph.extractors.base import BaseExtractor
from graph.db.entities import ExtractionContext, ExtractionResult, GraphEdge, GraphNode
from graph.db import schema as S

logger = logging.getLogger(__name__)

_HANDLED = re.compile(
    r'^(?:web|app|\w+\.exe)\.config$',
    re.IGNORECASE,
)

# <add name="DefaultConnection" connectionString="..." providerName="..." />
_CONN_STRING = re.compile(
    r'<add\s[^>]*\bname=["\']([^"\']+)["\'][^>]*\bconnectionString=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
# Also handle connectionString before name
_CONN_STRING2 = re.compile(
    r'<add\s[^>]*\bconnectionString=["\']([^"\']+)["\'][^>]*\bname=["\']([^"\']+)["\']',
    re.IGNORECASE,
)

# <add key="SomeUrl" value="https://..." />
_APP_SETTING = re.compile(
    r'<add\s[^>]*\bkey=["\']([^"\']+)["\'][^>]*\bvalue=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_APP_SETTING2 = re.compile(
    r'<add\s[^>]*\bvalue=["\']([^"\']+)["\'][^>]*\bkey=["\']([^"\']+)["\']',
    re.IGNORECASE,
)

# WCF client endpoint: <endpoint address="https://..." binding="..." contract="..." />
_WCF_ENDPOINT = re.compile(
    r'<endpoint\s[^>]*\baddress=["\']([^"\']+)["\'][^>]*\bcontract=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_WCF_ENDPOINT2 = re.compile(
    r'<endpoint\s[^>]*\bcontract=["\']([^"\']+)["\'][^>]*\baddress=["\']([^"\']+)["\']',
    re.IGNORECASE,
)

# URL detection
_URL_VALUE = re.compile(r'^https?://', re.IGNORECASE)
_URL_KEY = re.compile(
    r'(?:url|uri|endpoint|baseurl|baseaddress|host|address|server)s?$',
    re.IGNORECASE,
)

# DB name from connection string
_DB_NAME = re.compile(
    r'(?:Database|Initial\s+Catalog|Data\s+Source)=([^;]+)',
    re.IGNORECASE,
)


class WebConfigExtractor(BaseExtractor):
    def can_handle(self, file_path: str, text: str) -> bool:
        return (
            bool(_HANDLED.match(Path(file_path).name))
            and "<configuration" in text
        )

    def extract(self, file_path: str, text: str, context: ExtractionContext) -> ExtractionResult:
        result = ExtractionResult(source_file=file_path, extractor_name="WebConfigExtractor")
        repository = context.repository

        service_name = context.service_name or context.repository or Path(file_path).parent.name
        service_qname = f"Service:{repository}:{service_name}"

        result.nodes.append(GraphNode(
            label=S.LABEL_SERVICE,
            key="qualified_name",
            key_value=service_qname,
            properties={
                "name": service_name,
                "repository": repository,
                "source_file": file_path,
            },
        ))

        seen: set[str] = set()

        def _add_external(ext_qname: str, props: dict, conn_name: str) -> None:
            if ext_qname in seen:
                return
            seen.add(ext_qname)
            result.nodes.append(GraphNode(
                label=S.LABEL_EXTERNAL_SERVICE,
                key="qualified_name",
                key_value=ext_qname,
                properties={**props, "source_file": file_path},
            ))
            result.edges.append(GraphEdge(
                from_label=S.LABEL_SERVICE,
                from_key="qualified_name",
                from_key_value=service_qname,
                to_label=S.LABEL_EXTERNAL_SERVICE,
                to_key="qualified_name",
                to_key_value=ext_qname,
                rel_type=S.REL_CALLS_EXTERNAL,
                properties={"config_key": conn_name},
            ))

        # connectionStrings section
        for pattern in (_CONN_STRING, _CONN_STRING2):
            for m in pattern.finditer(text):
                name, conn_str = (m.group(1), m.group(2)) if pattern is _CONN_STRING else (m.group(2), m.group(1))
                db_name = _extract_db_name(conn_str) or name
                ext_qname = f"ExternalService:db:{name.lower()}"
                _add_external(ext_qname, {
                    "name": name,
                    "kind": "database",
                    "db_name": db_name,
                }, name)

        # appSettings — only URL-valued keys
        for pattern in (_APP_SETTING, _APP_SETTING2):
            for m in pattern.finditer(text):
                key, value = (m.group(1), m.group(2)) if pattern is _APP_SETTING else (m.group(2), m.group(1))
                if _URL_VALUE.match(value) and _URL_KEY.search(key):
                    ext_qname = f"ExternalService:url:{value.rstrip('/').lower()}"
                    _add_external(ext_qname, {
                        "name": key,
                        "url": value,
                        "kind": "http_service",
                    }, key)

        # WCF client endpoints
        for pattern in (_WCF_ENDPOINT, _WCF_ENDPOINT2):
            for m in pattern.finditer(text):
                address, contract = (m.group(1), m.group(2)) if pattern is _WCF_ENDPOINT else (m.group(2), m.group(1))
                if not _URL_VALUE.match(address):
                    continue
                ext_qname = f"ExternalService:wcf:{address.rstrip('/').lower()}"
                _add_external(ext_qname, {
                    "name": contract.split(".")[-1],
                    "url": address,
                    "contract": contract,
                    "kind": "wcf_service",
                }, contract)

        return result


def _extract_db_name(conn_str: str) -> str | None:
    m = _DB_NAME.search(conn_str)
    return m.group(1).strip() if m else None
