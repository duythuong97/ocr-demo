"""Python AST call-graph extractor.

Uses Python stdlib `ast` module (no extra dependency).

Extracts:
  - FunctionDef / AsyncFunctionDef  → Function node
  - ClassDef  → Class node
  - ast.Call  → CALLS edge (stub callee if not locally defined)
  - ast.Import / ast.ImportFrom  → IMPORTS edge + Module node
"""
from __future__ import annotations

import ast
import logging
from pathlib import Path

from graph.extractors.base import BaseExtractor
from graph.db.entities import ExtractionContext, ExtractionResult, GraphNode, GraphEdge
from graph.db import schema as S

logger = logging.getLogger(__name__)

_PY_EXTENSIONS = {".py"}

_PY_BUILTINS = {
    "print", "len", "range", "enumerate", "zip", "map", "filter", "sorted",
    "list", "dict", "set", "tuple", "str", "int", "float", "bool", "type",
    "isinstance", "issubclass", "hasattr", "getattr", "setattr", "delattr",
    "super", "object", "property", "staticmethod", "classmethod",
    "open", "format", "repr", "id", "hash", "abs", "round", "min", "max",
    "sum", "any", "all", "next", "iter", "vars", "dir", "callable",
    "append", "extend", "update", "get", "items", "keys", "values",
    "Exception", "ValueError", "TypeError", "KeyError", "IndexError",
    "AttributeError", "RuntimeError", "StopIteration", "NotImplementedError",
}


class PythonAstExtractor(BaseExtractor):
    """Call-graph extractor for Python files using stdlib ast."""

    def can_handle(self, file_path: str, text: str) -> bool:
        return Path(file_path).suffix.lower() in _PY_EXTENSIONS

    def extract(self, file_path: str, text: str, context: ExtractionContext) -> ExtractionResult:
        result = ExtractionResult(source_file=file_path, extractor_name="PythonAstExtractor")
        try:
            tree = ast.parse(text, filename=file_path)
        except SyntaxError as exc:
            logger.debug("Python AST parse failed for %s: %s", file_path, exc)
            return result

        repo = context.repository or Path(file_path).parts[0]
        file_stem = Path(file_path).stem

        # file-level module node
        mod_qname = f"Module:{repo}:{file_stem}"
        result.nodes.append(GraphNode(
            label=S.LABEL_MODULE, key="qualified_name", key_value=mod_qname,
            properties={"name": file_stem, "repository": repo},
        ))

        local_fns: dict[str, str] = {}   # simple_name -> qname
        local_classes: set[str] = set()

        try:
            self._walk(tree, file_path, file_stem, repo, result, local_fns, local_classes, mod_qname, None, None)
        except Exception as exc:
            logger.warning("PythonAstExtractor walk failed for %s: %s", file_path, exc)

        _deduplicate(result)
        return result

    def _walk(
        self,
        node: ast.AST,
        file_path: str,
        file_stem: str,
        repo: str,
        result: ExtractionResult,
        local_fns: dict[str, str],
        local_classes: set[str],
        mod_qname: str,
        current_class: str | None,
        current_fn: str | None,
    ) -> None:
        if isinstance(node, ast.ClassDef):
            cls_name = node.name
            local_classes.add(cls_name)
            cls_qname = f"Class:{repo}:{cls_name}"
            result.nodes.append(GraphNode(
                label=S.LABEL_CLASS, key="qualified_name", key_value=cls_qname,
                properties={"name": cls_name, "repository": repo, "file": file_stem},
            ))
            for child in ast.iter_child_nodes(node):
                self._walk(child, file_path, file_stem, repo, result, local_fns, local_classes, mod_qname, cls_name, None)
            return

        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            fn_name = node.name
            qual = f"{current_class}.{fn_name}" if current_class else fn_name
            fn_qname = f"Function:{repo}:{qual}"
            local_fns[fn_name] = fn_qname
            result.nodes.append(GraphNode(
                label=S.LABEL_FUNCTION, key="qualified_name", key_value=fn_qname,
                properties={"name": qual, "repository": repo, "file": file_stem,
                            "is_async": isinstance(node, ast.AsyncFunctionDef)},
            ))
            if current_class:
                cls_qname = f"Class:{repo}:{current_class}"
                result.edges.append(GraphEdge(
                    from_label=S.LABEL_FUNCTION, from_key="qualified_name", from_key_value=fn_qname,
                    to_label=S.LABEL_CLASS, to_key="qualified_name", to_key_value=cls_qname,
                    rel_type=S.REL_BELONGS_TO,
                ))
            for child in ast.iter_child_nodes(node):
                self._walk(child, file_path, file_stem, repo, result, local_fns, local_classes, mod_qname, current_class, fn_qname)
            return

        if isinstance(node, ast.Call) and current_fn:
            callee_simple = _call_name(node)
            if callee_simple and callee_simple not in _PY_BUILTINS and len(callee_simple) > 1:
                callee_qname = local_fns.get(callee_simple) or f"Function:{repo}:{callee_simple}"
                if callee_qname != current_fn:
                    result.edges.append(GraphEdge(
                        from_label=S.LABEL_FUNCTION, from_key="qualified_name", from_key_value=current_fn,
                        to_label=S.LABEL_FUNCTION, to_key="qualified_name", to_key_value=callee_qname,
                        rel_type=S.REL_CALLS,
                    ))

        if isinstance(node, ast.Import):
            for alias in node.names:
                mod_name = alias.name
                imp_qname = f"Module:{repo}:{mod_name}"
                result.nodes.append(GraphNode(
                    label=S.LABEL_MODULE, key="qualified_name", key_value=imp_qname,
                    properties={"name": mod_name, "repository": repo},
                ))
                result.edges.append(GraphEdge(
                    from_label=S.LABEL_MODULE, from_key="qualified_name", from_key_value=mod_qname,
                    to_label=S.LABEL_MODULE, to_key="qualified_name", to_key_value=imp_qname,
                    rel_type=S.REL_IMPORTS,
                ))

        if isinstance(node, ast.ImportFrom):
            mod_name = node.module or ""
            if mod_name:
                imp_qname = f"Module:{repo}:{mod_name}"
                result.nodes.append(GraphNode(
                    label=S.LABEL_MODULE, key="qualified_name", key_value=imp_qname,
                    properties={"name": mod_name, "repository": repo},
                ))
                result.edges.append(GraphEdge(
                    from_label=S.LABEL_MODULE, from_key="qualified_name", from_key_value=mod_qname,
                    to_label=S.LABEL_MODULE, to_key="qualified_name", to_key_value=imp_qname,
                    rel_type=S.REL_IMPORTS,
                ))

        for child in ast.iter_child_nodes(node):
            self._walk(child, file_path, file_stem, repo, result, local_fns, local_classes, mod_qname, current_class, current_fn)


def _call_name(node: ast.Call) -> str | None:
    """Extract the simple function name from a Call node."""
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


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
