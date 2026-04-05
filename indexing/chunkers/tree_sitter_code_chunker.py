from __future__ import annotations

from pathlib import Path

from indexing.chunkers.base import BaseChunker, SemanticChunk
from indexing.chunkers.text_chunker import TextChunker

# Extension -> tree_sitter_languages parser name
_LANGUAGE_BY_EXT = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".java": "java",
    ".go": "go",
    ".sql": "sql",
    ".sh": "bash",
    ".rb": "ruby",
    ".kt": "kotlin",
    ".swift": "swift",
    ".rs": "rust",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
}

_WANTED_NODES = {
    "python": {"function_definition", "class_definition"},
    "javascript": {
        "function_declaration",
        "method_definition",
        "class_declaration",
        "arrow_function",
    },
    "typescript": {
        "function_declaration",
        "method_definition",
        "class_declaration",
        "interface_declaration",
        "type_alias_declaration",
    },
    "tsx": {
        "function_declaration",
        "method_definition",
        "class_declaration",
        "interface_declaration",
        "type_alias_declaration",
    },
    "java": {
        "class_declaration",
        "interface_declaration",
        "enum_declaration",
        "method_declaration",
        "constructor_declaration",
    },
    "go": {"function_declaration", "method_declaration", "type_declaration"},
    "sql": {
        "select_statement",
        "insert_statement",
        "update_statement",
        "delete_statement",
        "create_table_statement",
        "alter_table_statement",
        "create_view_statement",
    },
    "bash": {"function_definition"},
    "ruby": {"method", "singleton_method", "class", "module"},
    "kotlin": {
        "function_declaration",
        "class_declaration",
        "object_declaration",
        "secondary_constructor",
    },
    "swift": {
        "function_declaration",
        "class_declaration",
        "struct_declaration",
        "protocol_declaration",
        "extension_declaration",
    },
    "rust": {
        "function_item",
        "impl_item",
        "struct_item",
        "enum_item",
        "trait_item",
    },
    "c": {
        "function_definition",
        "struct_specifier",
        "enum_specifier",
    },
    "cpp": {
        "function_definition",
        "class_specifier",
        "struct_specifier",
        "enum_specifier",
    },
}


class TreeSitterCodeChunker(BaseChunker):
    """AST-driven chunker for code files using tree_sitter_languages.

    Falls back to TextChunker when parser is unavailable for the file extension
    or when parsing yields no useful AST chunks.
    """

    def __init__(self, fallback: TextChunker | None = None) -> None:
        self._fallback = fallback or TextChunker(chunk_chars=1200, overlap=180)
        self._parsers: dict[str, object | None] = {}
        self._get_parser_fn = None

    def _get_parser(self, parser_name: str):
        if parser_name in self._parsers:
            return self._parsers[parser_name]

        if self._get_parser_fn is None:
            try:
                from tree_sitter_languages import get_parser

                self._get_parser_fn = get_parser
            except Exception:
                self._get_parser_fn = False

        if not self._get_parser_fn:
            self._parsers[parser_name] = None
            return None

        try:
            parser = self._get_parser_fn(parser_name)
        except Exception:
            parser = None
        self._parsers[parser_name] = parser
        return parser

    def chunk(self, file_path: str, text: str) -> list[SemanticChunk]:
        ext = Path(file_path).suffix.lower()
        parser_name = _LANGUAGE_BY_EXT.get(ext)
        if not parser_name:
            return self._fallback.chunk(file_path, text)

        parser = self._get_parser(parser_name)
        if parser is None:
            return self._fallback.chunk(file_path, text)

        source_bytes = text.encode("utf-8", errors="ignore")
        tree = parser.parse(source_bytes)
        if tree is None:
            return self._fallback.chunk(file_path, text)

        wanted = _WANTED_NODES.get(parser_name, set())
        chunks: list[SemanticChunk] = []

        def walk(node):
            if wanted and node.type in wanted:
                snippet = (
                    source_bytes[node.start_byte : node.end_byte]
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
        return chunks if chunks else self._fallback.chunk(file_path, text)
