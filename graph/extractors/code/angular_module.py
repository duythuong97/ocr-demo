"""NgModuleExtractor — parse Angular @NgModule decorator in *.module.ts files.

Extracts:
  - The module itself → Module node
  - imports: [OtherModule, ...] → Module DEPENDS_ON Module
  - declarations: [MyComponent, ...] → Module CONTAINS FrontendComponent
  - providers: [MyService, ...] → Module DEPENDS_ON Service (internal service)
  - exports: [SharedComp, ...] → Module CONTAINS FrontendComponent (exported)

Node types: Module, FrontendComponent, Service
Relationships: DEPENDS_ON, CONTAINS
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from graph.extractors.base import BaseExtractor
from graph.db.entities import ExtractionContext, ExtractionResult, GraphEdge, GraphNode
from graph.db import schema as S

logger = logging.getLogger(__name__)

# Only .ts files named *.module.ts
_MODULE_NAME_RE = re.compile(r'\.module\.ts$', re.IGNORECASE)

# @NgModule({ ... }) — capture the full decorator body (handles multi-line)
_NG_MODULE_BLOCK = re.compile(r'@NgModule\s*\(\s*\{([\s\S]*?)\}\s*\)', re.IGNORECASE)

# export class SomethingModule
_MODULE_CLASS = re.compile(r'class\s+(\w+Module)\b', re.IGNORECASE)

# Extract array values from a named key, e.g.  imports: [A, B, \n  C]
_ARRAY_KEY = re.compile(
    r'(\w+)\s*:\s*\[([\s\S]*?)\]',
)

# Split identifiers from an array body (skip spread ...X and forRoot/forChild calls)
_IDENT = re.compile(r'\b([A-Z]\w+?)(?:\s*\.(?:forRoot|forChild|forFeature)\s*\([^)]*\))?\b')

# Angular built-in modules to skip (they live in @angular/*)
_ANGULAR_BUILTINS = re.compile(
    r'^(?:BrowserModule|CommonModule|HttpClientModule|FormsModule|ReactiveFormsModule'
    r'|RouterModule|BrowserAnimationsModule|NoopAnimationsModule'
    r'|MatModule|Cdk\w+|Store\w+|Effects\w+|NgRx\w+)$'
)


class NgModuleExtractor(BaseExtractor):
    def can_handle(self, file_path: str, text: str) -> bool:
        return (
            bool(_MODULE_NAME_RE.search(file_path))
            and "@NgModule" in text
        )

    def extract(self, file_path: str, text: str, context: ExtractionContext) -> ExtractionResult:
        result = ExtractionResult(source_file=file_path, extractor_name="NgModuleExtractor")
        repository = context.repository

        # Module class name
        m_class = _MODULE_CLASS.search(text)
        module_name = m_class.group(1) if m_class else Path(file_path).stem
        module_qname = f"Module:{repository}:{module_name}"

        result.nodes.append(GraphNode(
            label=S.LABEL_MODULE,
            key="qualified_name",
            key_value=module_qname,
            properties={
                "name": module_name,
                "repository": repository,
                "source_file": file_path,
                "kind": "angular_module",
                "layer": 4,
            },
        ))

        # Parse @NgModule({...}) body
        m_block = _NG_MODULE_BLOCK.search(text)
        if not m_block:
            return result

        body = m_block.group(1)
        arrays = _parse_arrays(body)

        # imports: OtherModule → DEPENDS_ON
        for ident in arrays.get("imports", []):
            if _ANGULAR_BUILTINS.match(ident):
                continue
            dep_qname = f"Module:{repository}:{ident}"
            result.nodes.append(GraphNode(
                label=S.LABEL_MODULE,
                key="qualified_name",
                key_value=dep_qname,
                properties={"name": ident, "repository": repository},
            ))
            result.edges.append(GraphEdge(
                from_label=S.LABEL_MODULE,
                from_key="qualified_name",
                from_key_value=module_qname,
                to_label=S.LABEL_MODULE,
                to_key="qualified_name",
                to_key_value=dep_qname,
                rel_type=S.REL_DEPENDS_ON,
            ))

        # declarations + exports: Component/Directive/Pipe → CONTAINS
        for ident in arrays.get("declarations", []) + arrays.get("exports", []):
            comp_qname = f"FrontendComponent:{repository}:{ident}"
            result.nodes.append(GraphNode(
                label=S.LABEL_FRONTEND_COMPONENT,
                key="qualified_name",
                key_value=comp_qname,
                properties={
                    "name": ident,
                    "repository": repository,
                    "source_file": file_path,
                    "kind": "angular_component",
                },
            ))
            result.edges.append(GraphEdge(
                from_label=S.LABEL_MODULE,
                from_key="qualified_name",
                from_key_value=module_qname,
                to_label=S.LABEL_FRONTEND_COMPONENT,
                to_key="qualified_name",
                to_key_value=comp_qname,
                rel_type=S.REL_CONTAINS,
            ))

        # providers: SomeService → DEPENDS_ON (internal services)
        for ident in arrays.get("providers", []):
            # Skip tokens that look like Angular injection config, not plain services
            if ident in ("APP_INITIALIZER", "HTTP_INTERCEPTORS", "ErrorHandler"):
                continue
            svc_qname = f"Service:{repository}:{ident}"
            result.nodes.append(GraphNode(
                label=S.LABEL_SERVICE,
                key="qualified_name",
                key_value=svc_qname,
                properties={"name": ident, "repository": repository, "kind": "angular_service"},
            ))
            result.edges.append(GraphEdge(
                from_label=S.LABEL_MODULE,
                from_key="qualified_name",
                from_key_value=module_qname,
                to_label=S.LABEL_SERVICE,
                to_key="qualified_name",
                to_key_value=svc_qname,
                rel_type=S.REL_DEPENDS_ON,
            ))

        return result


def _parse_arrays(body: str) -> dict[str, list[str]]:
    """Extract named array keys from @NgModule decorator body."""
    result: dict[str, list[str]] = {}
    for m in _ARRAY_KEY.finditer(body):
        key = m.group(1)
        content = m.group(2)
        idents = [i.group(1) for i in _IDENT.finditer(content)]
        if idents:
            result.setdefault(key, []).extend(idents)
    return result
