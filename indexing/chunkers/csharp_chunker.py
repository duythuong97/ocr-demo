from __future__ import annotations

from indexing.chunkers.base import BaseChunker, SemanticChunk
from indexing.chunkers.text_chunker import TextChunker

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
    except Exception:
        pass

    # Strategy 2: tree-sitter < 0.21 (older API)
    try:
        import tree_sitter_c_sharp
        from tree_sitter import Language, Parser

        lang = Language(tree_sitter_c_sharp.language_c_sharp(), "c_sharp")
        parser = Parser()
        parser.set_language(lang)
        return parser
    except Exception:
        pass

    # Strategy 3: tree-sitter-languages bundle (Python <= 3.12 only)
    try:
        from tree_sitter_languages import get_parser

        return get_parser("c_sharp")
    except Exception:
        pass

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

        def walk(node):
            if node.type in _WANTED_NODES:
                snippet = (
                    source_bytes[node.start_byte: node.end_byte]
                    .decode("utf-8", errors="ignore")
                    .strip()
                )
                if snippet:
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
                walk(child)

        walk(tree.root_node)
        return chunks if chunks else _fallback.chunk(file_path, text)
