"""C# AST call-graph extractor.

Uses tree-sitter c_sharp grammar to extract:
  - namespace_declaration  → Module node
  - class_declaration  → Class node
  - method_declaration / constructor_declaration  → Function node
  - invocation_expression  → CALLS edge
  - object_creation_expression  → INSTANTIATES edge
  - using_directive  → IMPORTS edge + Module node

Runs ALONGSIDE the existing CSharpSqlExtractor (additive).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from graph.db.entities import ExtractionContext, ExtractionResult, GraphNode, GraphEdge
from graph.db import schema as S
from .ast_extractor_base import AstCallExtractorBase, _node_text, _child_by_type

logger = logging.getLogger(__name__)

_CS_EXTENSIONS = {".cs"}

_CSHARP_BUILTINS = {
    "Console", "Math", "Environment", "Convert", "DateTime", "TimeSpan",
    "Guid", "Encoding", "File", "Directory", "Path", "Stream", "string",
    "int", "long", "bool", "double", "object", "var",
}


class CSharpAstExtractor(AstCallExtractorBase):
    """AST call-graph extractor for C# source files."""

    ts_language = "c_sharp"
    handled_extensions = _CS_EXTENSIONS

    def _extract_from_tree(self, file_path: str, src: bytes, tree: Any, context: ExtractionContext) -> ExtractionResult:
        result = ExtractionResult()
        repo = context.repository or Path(file_path).parts[0]
        file_stem = Path(file_path).stem

        local_fns: dict[str, str] = {}  # simple_name -> qname

        def walk(node: Any, ns: str, current_class: str | None, current_fn: str | None) -> None:
            t = node.type

            if t == "namespace_declaration":
                name_node = _child_by_type(node, "identifier", "qualified_name")
                ns_name = _node_text(name_node, src) if name_node else "Unknown"
                mod_qname = f"Module:{repo}:{ns_name}"
                result.nodes.append(GraphNode(
                    label=S.LABEL_MODULE, key="qualified_name", key_value=mod_qname,
                    properties={"name": ns_name, "repository": repo},
                ))
                for child in node.children:
                    walk(child, ns_name, None, None)
                return

            if t == "using_directive":
                name_node = _child_by_type(node, "identifier", "qualified_name", "name_colon")
                if name_node:
                    mod_name = _node_text(name_node, src)
                    mod_qname = f"Module:{repo}:{mod_name}"
                    result.nodes.append(GraphNode(
                        label=S.LABEL_MODULE, key="qualified_name", key_value=mod_qname,
                        properties={"name": mod_name, "repository": repo},
                    ))
                    if current_class:
                        from_qname = f"Class:{repo}:{current_class}"
                        from_label = S.LABEL_CLASS
                    elif ns:
                        from_qname = f"Module:{repo}:{ns}"
                        from_label = S.LABEL_MODULE
                    else:
                        from_qname = f"Module:{repo}:{file_stem}"
                        from_label = S.LABEL_MODULE
                    result.edges.append(GraphEdge(
                        from_label=from_label, from_key="qualified_name", from_key_value=from_qname,
                        to_label=S.LABEL_MODULE, to_key="qualified_name", to_key_value=mod_qname,
                        rel_type=S.REL_IMPORTS,
                    ))
                return

            if t == "class_declaration":
                name_node = _child_by_type(node, "identifier")
                cls_name = _node_text(name_node, src) if name_node else "AnonymousClass"
                full_cls = f"{ns}.{cls_name}" if ns else cls_name
                cls_qname = f"Class:{repo}:{full_cls}"
                result.nodes.append(self._class_node(cls_qname, full_cls, repo, {"namespace": ns, "file": file_stem}))
                for child in node.children:
                    walk(child, ns, full_cls, None)
                return

            if t in ("method_declaration", "local_function_statement"):
                name_node = _child_by_type(node, "identifier")
                mth_name = _node_text(name_node, src) if name_node else "anonymousMethod"
                qual = f"{current_class}.{mth_name}" if current_class else mth_name
                fn_qname = f"Function:{repo}:{qual}"
                local_fns[mth_name] = fn_qname
                result.nodes.append(self._function_node(fn_qname, qual, repo, {"file": file_stem}))
                if current_class:
                    cls_qname = f"Class:{repo}:{current_class}"
                    result.edges.append(self._belongs_to_edge(fn_qname, cls_qname))
                for child in node.children:
                    walk(child, ns, current_class, fn_qname)
                return

            if t == "constructor_declaration":
                name_node = _child_by_type(node, "identifier")
                ctor_name = _node_text(name_node, src) if name_node else ".ctor"
                qual = f"{current_class}.{ctor_name}" if current_class else ctor_name
                fn_qname = f"Function:{repo}:{qual}"
                local_fns[ctor_name] = fn_qname
                result.nodes.append(self._function_node(fn_qname, qual, repo, {"file": file_stem}))
                if current_class:
                    cls_qname = f"Class:{repo}:{current_class}"
                    result.edges.append(self._belongs_to_edge(fn_qname, cls_qname))
                for child in node.children:
                    walk(child, ns, current_class, fn_qname)
                return

            if t == "invocation_expression" and current_fn:
                fn_node = node.children[0] if node.children else None
                if fn_node:
                    raw = _node_text(fn_node, src)
                    callee_simple = raw.split(".")[-1].split("(")[0]
                    if callee_simple and callee_simple not in _CSHARP_BUILTINS and len(callee_simple) > 1:
                        callee_qname = local_fns.get(callee_simple) or f"Function:{repo}:{callee_simple}"
                        if callee_qname != current_fn:
                            result.edges.append(self._calls_edge(current_fn, callee_qname))

            if t == "object_creation_expression" and current_fn:
                type_node = _child_by_type(node, "identifier", "generic_name", "qualified_name")
                if type_node:
                    cls_name = _node_text(type_node, src).split("<")[0]
                    if cls_name and cls_name[0].isupper() and cls_name not in _CSHARP_BUILTINS:
                        full_cls = f"{ns}.{cls_name}" if ns else cls_name
                        cls_qname = f"Class:{repo}:{full_cls}"
                        result.edges.append(self._instantiates_edge(current_fn, cls_qname))

            for child in node.children:
                walk(child, ns, current_class, current_fn)

        walk(tree.root_node, "", None, None)
        _deduplicate(result)
        return result


def _deduplicate(result: ExtractionResult) -> None:
    seen_nodes: set[tuple] = set()
    seen_edges: set[tuple] = set()
    nodes, edges = [], []
    for n in result.nodes:
        key = (n.label, n.key, n.key_value)
        if key not in seen_nodes:
            seen_nodes.add(key)
            nodes.append(n)
    for e in result.edges:
        key = (e.from_key_value, e.rel_type, e.to_key_value)
        if key not in seen_edges:
            seen_edges.add(key)
            edges.append(e)
    result.nodes = nodes
    result.edges = edges
