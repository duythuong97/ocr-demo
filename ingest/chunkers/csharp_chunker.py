from __future__ import annotations

import logging

from ingest.chunkers.base import BaseChunker, SemanticChunk
from ingest.chunkers.text_chunker import TextChunker

logger = logging.getLogger(__name__)

_WANTED_NODES = {
    "class_declaration",
    "struct_declaration",
    "interface_declaration",
    "enum_declaration",
    "method_declaration",
    "constructor_declaration",
    "property_declaration",
    "record_declaration",
}

_fallback = TextChunker(chunk_chars=1200, overlap=180)


def _load_csharp_parser():
    """Try loading a tree-sitter C# parser via three strategies."""
    # Strategy 1: tree-sitter >= 0.21 + tree-sitter-c-sharp (Python 3.13+)
    try:
        import tree_sitter_c_sharp
        from tree_sitter import Language, Parser

        lang = Language(tree_sitter_c_sharp.language())
        return Parser(lang)
    except Exception as exc:
        logger.debug("tree-sitter strategy 1 (new API) unavailable: %s", exc)

    # Strategy 2: tree-sitter < 0.21 (older API)
    try:
        import tree_sitter_c_sharp
        from tree_sitter import Language, Parser

        lang = Language(tree_sitter_c_sharp.language_c_sharp(), "c_sharp")
        parser = Parser()
        parser.set_language(lang)
        return parser
    except Exception as exc:
        logger.debug("tree-sitter strategy 2 (old API) unavailable: %s", exc)

    # Strategy 3: tree-sitter-languages bundle (Python <= 3.12 only)
    try:
        from tree_sitter_languages import get_parser

        return get_parser("c_sharp")
    except Exception as exc:
        logger.debug("tree-sitter strategy 3 (bundle) unavailable: %s", exc)

    return None


class CSharpChunker(BaseChunker):
    """AST-based chunker for C# files using tree-sitter.

    Falls back to :class:`TextChunker` when tree-sitter is unavailable or
    the parse yields no recognised top-level nodes.
    """

    def __init__(self) -> None:
        self._parser = None
        self._tried = False

    def _get_parser(self):
        if not self._tried:
            self._tried = True
            self._parser = _load_csharp_parser()
        return self._parser

    def chunk(self, file_path: str, text: str) -> list[SemanticChunk]:
        parser = self._get_parser()
        if parser is None:
            return _fallback.chunk(file_path, text)

        source_bytes = text.encode("utf-8", errors="ignore")
        tree = parser.parse(source_bytes)
        if tree is None:
            return _fallback.chunk(file_path, text)

        chunks: list[SemanticChunk] = []

        def _extract_name(node) -> str:
            """Return text of the first qualified_name/identifier/name child."""
            name_node = next(
                (
                    c
                    for c in node.children
                    if c.type in ("qualified_name", "identifier", "name")
                ),
                None,
            )
            if name_node is None:
                return ""
            return (
                source_bytes[name_node.start_byte : name_node.end_byte]
                .decode("utf-8", errors="ignore")
                .strip()
            )

        # Pre-scan root for a file-scoped namespace declaration (C# 10+).
        # These appear as siblings of top-level class declarations at
        # compilation_unit level, not as parents.
        file_ns = ""
        for top in tree.root_node.children:
            if top.type == "file_scoped_namespace_declaration":
                file_ns = _extract_name(top)
                break

        def walk(node, ns_prefix: str = "") -> None:
            # Handle block-style namespace declarations (traditional syntax)
            if node.type == "namespace_declaration":
                ns_name = _extract_name(node)
                new_prefix = f"{ns_prefix}.{ns_name}" if ns_prefix else ns_name
                for child in node.children:
                    walk(child, new_prefix)
                return

            # Skip file_scoped_namespace_declaration nodes — already handled above
            if node.type == "file_scoped_namespace_declaration":
                return

            if node.type in _WANTED_NODES:
                snippet = (
                    source_bytes[node.start_byte : node.end_byte]
                    .decode("utf-8", errors="ignore")
                    .strip()
                )
                if snippet:
                    if ns_prefix:
                        snippet = f"// namespace {ns_prefix}\n{snippet}"
                    chunks.append(
                        SemanticChunk(
                            chunk_id=f"{node.type}-{node.start_byte}-{node.end_byte}",
                            chunk_type=node.type,
                            text=snippet,
                        )
                    )
                # Do not recurse into children of a matched node to avoid
                # duplicating content between parent and child chunks.
                return
            for child in node.children:
                walk(child, ns_prefix)

        walk(tree.root_node, ns_prefix=file_ns)
        return chunks if chunks else _fallback.chunk(file_path, text)
