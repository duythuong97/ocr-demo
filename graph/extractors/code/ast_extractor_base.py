"""AstCallExtractorBase — shared tree-sitter parser cache + helpers for AST call graph extractors.

Sub-classes implement `_extract_from_tree()` for their specific language grammar.
"""
from __future__ import annotations

import logging
import warnings
from pathlib import Path
from typing import Any

from graph.extractors.base import BaseExtractor
from graph.db.entities import ExtractionContext, ExtractionResult, GraphNode, GraphEdge
from graph.db import schema as S

logger = logging.getLogger(__name__)

# Shared parser cache (process-wide)
_PARSER_CACHE: dict[str, Any] = {}
_GET_PARSER_FN = None


def _get_ts_parser(language: str) -> Any:
    """Return a cached tree-sitter parser for *language*, or None if unavailable."""
    global _GET_PARSER_FN
    if language in _PARSER_CACHE:
        return _PARSER_CACHE[language]
    try:
        if _GET_PARSER_FN is None:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", FutureWarning)
                from tree_sitter_languages import get_parser as _gp
            _GET_PARSER_FN = _gp
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            parser = _GET_PARSER_FN(language)
        _PARSER_CACHE[language] = parser
        return parser
    except Exception as exc:
        logger.debug("Tree-sitter parser for '%s' unavailable: %s", language, exc)
        _PARSER_CACHE[language] = None
        return None


def _node_text(node: Any, src_bytes: bytes) -> str:
    return src_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _child_by_type(node: Any, *types: str) -> Any | None:
    for child in node.children:
        if child.type in types:
            return child
    return None


def _children_by_type(node: Any, *types: str) -> list[Any]:
    return [c for c in node.children if c.type in types]


class AstCallExtractorBase(BaseExtractor):
    """Base class for AST-based call-graph extractors.

    Sub-classes must implement:
        ts_language      : str  — tree-sitter language name
        handled_extensions : set[str]  — e.g. {".ts", ".tsx"}
        _extract_from_tree(file_path, src_bytes, tree, context) -> ExtractionResult
    """

    ts_language: str = ""
    handled_extensions: set[str] = set()

    def can_handle(self, file_path: str, text: str) -> bool:
        return Path(file_path).suffix.lower() in self.handled_extensions

    def extract(self, file_path: str, text: str, context: ExtractionContext) -> ExtractionResult:
        result = ExtractionResult(source_file=file_path, extractor_name=self.__class__.__name__)
        parser = _get_ts_parser(self.ts_language)
        if parser is None:
            return result
        try:
            src_bytes = text.encode("utf-8")
            tree = parser.parse(src_bytes)
            result = self._extract_from_tree(file_path, src_bytes, tree, context)
            result.source_file = file_path
            result.extractor_name = self.__class__.__name__
        except Exception as exc:
            logger.warning("[%s] extraction failed for %s: %s", self.__class__.__name__, file_path, exc)
        return result

    def _extract_from_tree(
        self, file_path: str, src_bytes: bytes, tree: Any, context: ExtractionContext
    ) -> ExtractionResult:  # pragma: no cover
        raise NotImplementedError

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _function_node(self, qname: str, name: str, repository: str, extra: dict | None = None) -> GraphNode:
        props = {"name": name, "repository": repository}
        if extra:
            props.update(extra)
        return GraphNode(label=S.LABEL_FUNCTION, key="qualified_name", key_value=qname, properties=props)

    def _class_node(self, qname: str, name: str, repository: str, extra: dict | None = None) -> GraphNode:
        """Create a class node with the legacy LABEL_CLASS. Use _class_node_with_label() for specific labels."""
        props = {"name": name, "repository": repository}
        if extra:
            props.update(extra)
        return GraphNode(label=S.LABEL_CLASS, key="qualified_name", key_value=qname, properties=props)

    def _class_node_with_label(
        self, label: str, qname: str, name: str, repository: str, extra: dict | None = None
    ) -> GraphNode:
        """Create a class node with an explicit label (ApiController, ServiceClass, RepositoryClass, etc.)."""
        props = {"name": name, "repository": repository}
        if extra:
            props.update(extra)
        return GraphNode(label=label, key="qualified_name", key_value=qname, properties=props)

    def _module_node(self, qname: str, name: str, repository: str) -> GraphNode:
        return GraphNode(
            label=S.LABEL_MODULE, key="qualified_name", key_value=qname,
            properties={"name": name, "repository": repository},
        )

    def _calls_edge(self, caller_qname: str, callee_qname: str) -> GraphEdge:
        return GraphEdge(
            from_label=S.LABEL_FUNCTION, from_key="qualified_name", from_key_value=caller_qname,
            to_label=S.LABEL_FUNCTION, to_key="qualified_name", to_key_value=callee_qname,
            rel_type=S.REL_CALLS,
        )

    def _imports_edge(self, from_label: str, from_qname: str, to_label: str, to_qname: str) -> GraphEdge:
        return GraphEdge(
            from_label=from_label, from_key="qualified_name", from_key_value=from_qname,
            to_label=to_label, to_key="qualified_name", to_key_value=to_qname,
            rel_type=S.REL_IMPORTS,
        )

    def _belongs_to_edge(
        self,
        fn_qname: str,
        cls_qname: str,
        from_label: str | None = None,
        to_label: str | None = None,
    ) -> GraphEdge:
        return GraphEdge(
            from_label=from_label or S.LABEL_FUNCTION, from_key="qualified_name", from_key_value=fn_qname,
            to_label=to_label or S.LABEL_CLASS, to_key="qualified_name", to_key_value=cls_qname,
            rel_type=S.REL_BELONGS_TO,
        )

    def _instantiates_edge(
        self,
        caller_qname: str,
        class_qname: str,
        to_label: str | None = None,
    ) -> GraphEdge:
        return GraphEdge(
            from_label=S.LABEL_FUNCTION, from_key="qualified_name", from_key_value=caller_qname,
            to_label=to_label or S.LABEL_CLASS, to_key="qualified_name", to_key_value=class_qname,
            rel_type=S.REL_INSTANTIATES,
        )
