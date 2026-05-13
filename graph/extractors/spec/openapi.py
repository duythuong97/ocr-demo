"""OpenAPI / Swagger extractor.

Parses OpenAPI 3.x and Swagger 2.x YAML or JSON spec files.

Creates:
  - ApiEndpoint nodes for each path+method
  - Service node (inferred from spec info.title or repository)
  - HANDLED_BY edges when operationId can be matched to a known function name
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from graph.extractors.base import BaseExtractor
from graph.db.entities import ExtractionContext, ExtractionResult, GraphEdge, GraphNode
from graph.db import schema as S

try:
    import yaml as _yaml
except ImportError:
    _yaml = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

_OPENAPI_FILENAMES = {
    "swagger.yaml", "swagger.yml", "swagger.json",
    "openapi.yaml", "openapi.yml", "openapi.json",
}
_OPENAPI_NAME_PATTERN = re.compile(r"openapi|swagger", re.IGNORECASE)
_AWS_GW_PATTERN = re.compile(r"x-amazon-apigateway", re.IGNORECASE)


class OpenApiExtractor(BaseExtractor):
    def can_handle(self, file_path: str, text: str) -> bool:
        name = Path(file_path).name.lower()
        if name in _OPENAPI_FILENAMES:
            return True
        if Path(file_path).suffix.lower() in {".yaml", ".yml", ".json"}:
            # Standard OpenAPI/Swagger by keyword in first 500 chars
            if _OPENAPI_NAME_PATTERN.search(text[:500]):
                return True
            # AWS API Gateway exports: OpenAPI 3.x + x-amazon-apigateway-* extensions
            if _AWS_GW_PATTERN.search(text[:2000]):
                return True
        return False

    def extract(self, file_path: str, text: str, context: ExtractionContext) -> ExtractionResult:
        result = ExtractionResult(source_file=file_path, extractor_name="OpenApiExtractor")
        spec = _parse_spec(file_path, text)
        if not spec:
            return result

        repository = context.repository
        # Infer service name from spec info.title
        info = spec.get("info", {})
        service_name = context.service_name or _slugify(info.get("title", "")) or repository
        version = info.get("version", "")

        svc_qname = f"{S.LABEL_SERVICE}:{repository}:{service_name}"
        result.nodes.append(GraphNode(
            label=S.LABEL_SERVICE,
            key="qualified_name",
            key_value=svc_qname,
            properties={
                "qualified_name": svc_qname,
                "name": service_name,
                "version": version,
                "repository": repository,
                "source_file": file_path,
            },
        ))

        paths = spec.get("paths", {})
        for path, path_item in paths.items():
            if not isinstance(path_item, dict):
                continue
            for method, operation in path_item.items():
                if method.lower() not in {"get", "post", "put", "patch", "delete", "head", "options"}:
                    continue
                if not isinstance(operation, dict):
                    continue

                operation_id = operation.get("operationId", "")
                summary = operation.get("summary", "")
                tags = operation.get("tags", [])
                domain = tags[0] if tags else service_name

                ep_qname = f"{S.LABEL_API_ENDPOINT}:{repository}:{method.upper()}:{path}"

                ep_props: dict = {
                    "qualified_name": ep_qname,
                    "method": method.upper(),
                    "path": path,
                    "operation_id": operation_id,
                    "summary": summary,
                    "domain": domain,
                    "service": service_name,
                    "repository": repository,
                    "source_file": file_path,
                }

                # AWS API Gateway integration metadata
                gw_integration = operation.get("x-amazon-apigateway-integration", {})
                if gw_integration:
                    ep_props["aws_integration_type"] = gw_integration.get("type", "")
                    ep_props["aws_integration_uri"] = gw_integration.get("uri", "")
                    ep_props["aws_http_method"] = gw_integration.get("httpMethod", "")
                    ep_props["aws_passthrough"] = gw_integration.get("passthroughBehavior", "")

                result.nodes.append(GraphNode(
                    label=S.LABEL_API_ENDPOINT,
                    key="qualified_name",
                    key_value=ep_qname,
                    properties=ep_props,
                ))

                # ApiEndpoint → Service
                result.edges.append(GraphEdge(
                    from_label=S.LABEL_API_ENDPOINT, from_key="qualified_name", from_key_value=ep_qname,
                    to_label=S.LABEL_SERVICE, to_key="qualified_name", to_key_value=svc_qname,
                    rel_type=S.REL_BELONGS_TO,
                ))

                # If operationId matches a C# method name pattern, create HANDLED_BY hint
                # (the actual Function node may not exist yet — Neo4j will hold the edge)
                if operation_id:
                    func_qname = f"{S.LABEL_FUNCTION}:{repository}:{operation_id}"
                    result.edges.append(GraphEdge(
                        from_label=S.LABEL_API_ENDPOINT, from_key="qualified_name", from_key_value=ep_qname,
                        to_label=S.LABEL_FUNCTION, to_key="qualified_name", to_key_value=func_qname,
                        rel_type=S.REL_HANDLED_BY,
                        properties={"operation_id": operation_id, "confidence": "operationId_match"},
                    ))

        return result


def _parse_spec(file_path: str, text: str) -> dict | None:
    ext = Path(file_path).suffix.lower()
    try:
        if ext in {".yaml", ".yml"}:
            if _yaml is None:
                logger.warning("PyYAML not installed — cannot parse %s. Run: pip install pyyaml", file_path)
                return None
            return _yaml.safe_load(text) or {}
        else:
            return json.loads(text)
    except Exception as exc:
        logger.warning("OpenApiExtractor: failed to parse %s: %s", file_path, exc)
        return None


def _slugify(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
