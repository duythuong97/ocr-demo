"""TypeScript / TSX AST call-graph extractor.

Uses tree-sitter-languages grammar to extract:
  - Class declarations  → Class node
  - Function / method declarations  → Function node
  - call_expression  → CALLS edge  (stub callee if not defined in same file)
  - import_statement  → IMPORTS edge + Module node

Runs ALONGSIDE the existing TypeScriptApiCallExtractor (additive).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from graph.db.entities import ExtractionContext, ExtractionResult, GraphNode, GraphEdge
from graph.db import schema as S
from .ast_extractor_base import AstCallExtractorBase, _node_text, _child_by_type, _children_by_type

logger = logging.getLogger(__name__)

_TS_EXTENSIONS = {".ts", ".tsx", ".js", ".jsx"}
_BUILTIN_CALLS = {
    "console", "Math", "Object", "Array", "JSON", "Promise",
    "setTimeout", "setInterval", "clearTimeout", "clearInterval",
    "parseInt", "parseFloat", "isNaN", "isFinite", "encodeURIComponent",
    "decodeURIComponent", "Error", "Date", "Map", "Set", "Symbol",
}


def _get_identifier_text(node: Any, src: bytes) -> str:
    for child in node.children:
        if child.type in ("identifier", "property_identifier", "type_identifier"):
            return _node_text(child, src)
    return _node_text(node, src)[:40]


class TypeScriptAstExtractor(AstCallExtractorBase):
    """AST call-graph extractor for TypeScript / JavaScript files."""

    ts_language = "typescript"
    handled_extensions = _TS_EXTENSIONS

    def can_handle(self, file_path: str, text: str) -> bool:
        ext = Path(file_path).suffix.lower()
        if ext in {".tsx", ".jsx"}:
            return True   # force tsx grammar for tsx — still use this class
        return ext in _TS_EXTENSIONS

    def extract(self, file_path: str, text: str, context: ExtractionContext) -> ExtractionResult:
        ext = Path(file_path).suffix.lower()
        lang = "tsx" if ext in {".tsx", ".jsx"} else "typescript"
        # temporarily override ts_language for this file
        orig = self.ts_language
        self.ts_language = lang
        result = super().extract(file_path, text, context)
        self.ts_language = orig
        return result

    def _extract_from_tree(self, file_path: str, src: bytes, tree: Any, context: ExtractionContext) -> ExtractionResult:
        result = ExtractionResult()
        repo = context.repository or Path(file_path).parts[0]
        file_stem = Path(file_path).stem

        # file-level module node
        file_qname = f"Module:{repo}:{file_stem}"
        result.nodes.append(self._module_node(file_qname, file_stem, repo))

        # collect class names to detect instantiations
        local_classes: set[str] = set()
        # collect defined functions/methods for call resolution
        local_fns: dict[str, str] = {}  # simple_name -> qname

        def walk(node: Any, current_class: str | None, current_fn: str | None) -> None:
            t = node.type

            if t == "class_declaration":
                name_node = _child_by_type(node, "type_identifier", "identifier")
                cls_name = _node_text(name_node, src) if name_node else "AnonymousClass"
                cls_qname = f"Class:{repo}:{cls_name}"
                local_classes.add(cls_name)
                result.nodes.append(self._class_node(cls_qname, cls_name, repo, {"file": file_stem}))
                for child in node.children:
                    walk(child, cls_name, None)
                return

            if t in ("function_declaration", "function_expression", "arrow_function",
                     "generator_function_declaration"):
                name_node = _child_by_type(node, "identifier")
                fn_name = _node_text(name_node, src) if name_node else None
                if fn_name:
                    qual = f"{current_class}.{fn_name}" if current_class else fn_name
                    fn_qname = f"Function:{repo}:{qual}"
                    local_fns[fn_name] = fn_qname
                    result.nodes.append(self._function_node(fn_qname, qual, repo, {"file": file_stem}))
                    if current_class:
                        cls_qname = f"Class:{repo}:{current_class}"
                        result.edges.append(self._belongs_to_edge(fn_qname, cls_qname))
                    for child in node.children:
                        walk(child, current_class, fn_qname)
                    return

            if t == "method_definition":
                name_node = _child_by_type(node, "property_identifier", "identifier")
                method_name = _node_text(name_node, src) if name_node else "anonymousMethod"
                qual = f"{current_class}.{method_name}" if current_class else method_name
                fn_qname = f"Function:{repo}:{qual}"
                local_fns[method_name] = fn_qname
                result.nodes.append(self._function_node(fn_qname, qual, repo, {"file": file_stem}))
                if current_class:
                    cls_qname = f"Class:{repo}:{current_class}"
                    result.edges.append(self._belongs_to_edge(fn_qname, cls_qname))
                for child in node.children:
                    walk(child, current_class, fn_qname)
                return

            if t == "call_expression" and current_fn:
                fn_node = node.children[0] if node.children else None
                if fn_node:
                    callee_raw = _node_text(fn_node, src)
                    callee_simple = callee_raw.split(".")[-1].split("(")[0]
                    if callee_simple and callee_simple not in _BUILTIN_CALLS and len(callee_simple) > 1:
                        callee_qname = local_fns.get(callee_simple) or f"Function:{repo}:{callee_simple}"
                        if callee_qname != current_fn:
                            result.edges.append(self._calls_edge(current_fn, callee_qname))

            if t == "new_expression" and current_fn:
                ctor_node = _child_by_type(node, "identifier", "type_identifier")
                if ctor_node:
                    cls_name = _node_text(ctor_node, src)
                    if cls_name and cls_name[0].isupper():
                        cls_qname = f"Class:{repo}:{cls_name}"
                        result.edges.append(self._instantiates_edge(current_fn, cls_qname))

            if t == "import_statement":
                _handle_import(node, src, repo, file_qname, result)
                return

            for child in node.children:
                walk(child, current_class, current_fn)

        walk(tree.root_node, None, None)
        _deduplicate(result)
        return result


def _handle_import(node: Any, src: bytes, repo: str, from_qname: str, result: ExtractionResult) -> None:
    """Parse import_statement → IMPORTS edge to a Module node."""
    # find the module string
    source_node = None
    for child in node.children:
        if child.type == "string":
            source_node = child
            break
    if not source_node:
        return
    raw = _node_text(source_node, src).strip("'\"")
    if not raw or raw.startswith("@types/"):
        return
    # strip relative path prefix for display
    mod_name = raw.lstrip("./").replace("/", ".")
    if not mod_name:
        return
    mod_qname = f"Module:{repo}:{mod_name}"
    result.nodes.append(GraphNode(
        label=S.LABEL_MODULE, key="qualified_name", key_value=mod_qname,
        properties={"name": mod_name, "repository": repo, "source_path": raw},
    ))
    result.edges.append(GraphEdge(
        from_label=S.LABEL_MODULE, from_key="qualified_name", from_key_value=from_qname,
        to_label=S.LABEL_MODULE, to_key="qualified_name", to_key_value=mod_qname,
        rel_type=S.REL_IMPORTS,
    ))


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
