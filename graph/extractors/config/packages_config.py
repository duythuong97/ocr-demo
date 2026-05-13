"""PackagesConfigExtractor — parse .NET Framework packages.config files.

.NET Framework projects store NuGet dependencies in a separate packages.config
file instead of <PackageReference> inside .csproj (which is the .NET Core style).

Example:
  <packages>
    <package id="Newtonsoft.Json" version="13.0.1" targetFramework="net45" />
  </packages>

Extracts:
  - Each <package id="..." version="..."> → ExternalService node
  - Service (inferred from parent folder) -[:DEPENDS_ON]-> ExternalService

Node types: Service (current project), ExternalService (NuGet package)
Relationships: DEPENDS_ON
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from graph.extractors.base import BaseExtractor
from graph.db.entities import ExtractionContext, ExtractionResult, GraphEdge, GraphNode
from graph.db import schema as S

logger = logging.getLogger(__name__)

# <package id="Newtonsoft.Json" version="13.0.1" targetFramework="net45" />
_PACKAGE = re.compile(
    r'<package\s+id=["\']([^"\']+)["\'][^>]*version=["\']([^"\']*)["\']',
    re.IGNORECASE,
)
# Also handle version before id attribute order
_PACKAGE_VER_FIRST = re.compile(
    r'<package\s+[^>]*\bid=["\']([^"\']+)["\'][^>]*>',
    re.IGNORECASE,
)
_VERSION_ATTR = re.compile(r'\bversion=["\']([^"\']+)["\']', re.IGNORECASE)


class PackagesConfigExtractor(BaseExtractor):
    def can_handle(self, file_path: str, text: str) -> bool:
        return Path(file_path).name.lower() == "packages.config" and "<packages" in text

    def extract(self, file_path: str, text: str, context: ExtractionContext) -> ExtractionResult:
        result = ExtractionResult(source_file=file_path, extractor_name="PackagesConfigExtractor")
        repository = context.repository

        # Infer project name from parent folder (packages.config lives beside .csproj)
        project_name = context.service_name or Path(file_path).parent.name
        project_qname = f"Service:{repository}:{project_name}"

        result.nodes.append(GraphNode(
            label=S.LABEL_SERVICE,
            key="qualified_name",
            key_value=project_qname,
            properties={
                "name": project_name,
                "repository": repository,
                "source_file": file_path,
                "kind": "dotnet_project",
            },
        ))

        for m in _PACKAGE.finditer(text):
            pkg_name = m.group(1).strip()
            version = m.group(2).strip()
            pkg_qname = f"ExternalService:nuget:{pkg_name.lower()}"

            result.nodes.append(GraphNode(
                label=S.LABEL_EXTERNAL_SERVICE,
                key="qualified_name",
                key_value=pkg_qname,
                properties={
                    "name": pkg_name,
                    "kind": "nuget",
                    "version": version,
                },
            ))
            result.edges.append(GraphEdge(
                from_label=S.LABEL_SERVICE,
                from_key="qualified_name",
                from_key_value=project_qname,
                to_label=S.LABEL_EXTERNAL_SERVICE,
                to_key="qualified_name",
                to_key_value=pkg_qname,
                rel_type=S.REL_DEPENDS_ON,
                properties={"version": version, "package_manager": "packages.config"},
            ))

        return result
