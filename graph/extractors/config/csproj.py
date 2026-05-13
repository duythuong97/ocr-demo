"""CsprojExtractor — parse .csproj files.

Extracts:
  - <PackageReference Include="..." Version="...">  → ExternalService node + Service DEPENDS_ON ExternalService
  - <ProjectReference Include="...\\OtherProject.csproj">  → Service node + Service DEPENDS_ON Service

Node types: Service (current project), ExternalService (NuGet), Service (referenced project)
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

# <PackageReference Include="Microsoft.EntityFrameworkCore" Version="7.0.0" />
_PKG_REF = re.compile(
    r'<PackageReference\s+Include=["\']([^"\']+)["\'][^>]*(?:Version=["\']([^"\']*)["\'])?',
    re.IGNORECASE,
)
# <ProjectReference Include="..\OtherProject\OtherProject.csproj" />
_PROJ_REF = re.compile(
    r'<ProjectReference\s+Include=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
# <AssemblyName>MyProject</AssemblyName> — fallback to filename if absent
_ASSEMBLY = re.compile(r'<AssemblyName>([^<]+)</AssemblyName>', re.IGNORECASE)
# <RootNamespace>...</RootNamespace>
_NAMESPACE = re.compile(r'<RootNamespace>([^<]+)</RootNamespace>', re.IGNORECASE)


class CsprojExtractor(BaseExtractor):
    def can_handle(self, file_path: str, text: str) -> bool:
        return Path(file_path).suffix.lower() == ".csproj"

    def extract(self, file_path: str, text: str, context: ExtractionContext) -> ExtractionResult:
        result = ExtractionResult(source_file=file_path, extractor_name="CsprojExtractor")

        # Determine this project's name
        m_assembly = _ASSEMBLY.search(text)
        project_name = m_assembly.group(1).strip() if m_assembly else Path(file_path).stem
        repository = context.repository or project_name
        # Include repository in qname to avoid collisions across repos
        project_qname = f"Service:{repository}:{project_name}"

        # Repository node
        repo_qname = context.repo_qname()
        result.nodes.append(GraphNode(
            label=S.LABEL_REPOSITORY,
            key="qualified_name",
            key_value=repo_qname,
            properties={
                "name": repository,
                "source": context.source or "git",
                "vcs_url": context.vcs_url,
                "repository_path": context.repository_path,
            },
        ))

        # Project (Service) node
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

        # Repository -[:CONTAINS]-> Service
        result.edges.append(GraphEdge(
            from_label=S.LABEL_REPOSITORY,
            from_key="qualified_name",
            from_key_value=repo_qname,
            to_label=S.LABEL_SERVICE,
            to_key="qualified_name",
            to_key_value=project_qname,
            rel_type=S.REL_CONTAINS,
        ))

        # NuGet PackageReference dependencies
        # Use same repo_qname so all loop iterations share the already-appended node
        for m in _PKG_REF.finditer(text):
            pkg_name = m.group(1).strip()
            version = (m.group(2) or "").strip()
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
                properties={"version": version},
            ))

        # ProjectReference (inter-project dependencies)
        for m in _PROJ_REF.finditer(text):
            ref_path = m.group(1).replace("\\", "/")
            ref_name = Path(ref_path).stem
            ref_qname = f"Service:{repository}:{ref_name}"

            result.nodes.append(GraphNode(
                label=S.LABEL_SERVICE,
                key="qualified_name",
                key_value=ref_qname,
                properties={
                    "name": ref_name,
                    "repository": repository,
                    "kind": "dotnet_project",
                },
            ))
            # Referenced project also belongs to same repository
            result.edges.append(GraphEdge(
                from_label=S.LABEL_REPOSITORY,
                from_key="qualified_name",
                from_key_value=repo_qname,
                to_label=S.LABEL_SERVICE,
                to_key="qualified_name",
                to_key_value=ref_qname,
                rel_type=S.REL_CONTAINS,
            ))
            result.edges.append(GraphEdge(
                from_label=S.LABEL_SERVICE,
                from_key="qualified_name",
                from_key_value=project_qname,
                to_label=S.LABEL_SERVICE,
                to_key="qualified_name",
                to_key_value=ref_qname,
                rel_type=S.REL_DEPENDS_ON,
                properties={"ref_path": ref_path},
            ))

        return result
