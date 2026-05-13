from __future__ import annotations

from pathlib import Path

from ingest.chunkers.base import BaseChunker, SemanticChunk
from ingest.chunkers.text_chunker import TextChunker

# Chunks larger than this will be split with sliding-window fallback to avoid
# exceeding the embedding model's context window (~8192 tokens ≈ 6000 chars).
_MAX_CHUNK_CHARS = 6000

_fallback = TextChunker(chunk_chars=1400, overlap=200)

# Node types that act as "class containers" — not emitted as chunks themselves
# (because they're excluded from _WANTED_NODES), but their name is captured
# and propagated as `heading` to child method/property chunks.
_CLASS_CONTAINER_TYPES = {
    "class_declaration",
    "class_definition",
    "class_specifier",
    "interface_declaration",
    "struct_declaration",
    "struct_specifier",
    "impl_item",
    "module",
}

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
    ".cs": "c_sharp",
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
    "c_sharp": {
        # Note: class_declaration is intentionally excluded so the walker
        # continues recursing into methods/properties inside large classes.
        "method_declaration",
        "constructor_declaration",
        "property_declaration",
        "enum_declaration",
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
    """AST-driven chunker for code files using tree_sitter_languages."""

    def __init__(self) -> None:
        self._parsers: dict[str, object] = {}
        self._get_parser_fn = None

    def _get_parser(self, parser_name: str):
        if parser_name in self._parsers:
            return self._parsers[parser_name]

        if self._get_parser_fn is None:
            try:
                import warnings
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", FutureWarning)
                    from tree_sitter_languages import get_parser
                self._get_parser_fn = get_parser
            except ImportError as exc:
                raise ImportError(
                    "tree_sitter_languages is not installed. Run: pip install tree_sitter_languages"
                ) from exc

        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            parser = self._get_parser_fn(parser_name)
        self._parsers[parser_name] = parser
        return parser

    def chunk(self, file_path: str, text: str) -> list[SemanticChunk]:
        ext = Path(file_path).suffix.lower()
        parser_name = _LANGUAGE_BY_EXT.get(ext)
        if not parser_name:
            raise ValueError(f"Unsupported extension for TreeSitterCodeChunker: {ext!r}")

        parser = self._get_parser(parser_name)

        source_bytes = text.encode("utf-8", errors="ignore")
        tree = parser.parse(source_bytes)
        if tree is None:
            raise ValueError(f"tree-sitter parse returned None for {file_path!r}")

        wanted = _WANTED_NODES.get(parser_name, set())
        chunks: list[SemanticChunk] = []
        chunk_counter = [0]  # mutable counter shared across recursive calls

        def _extract_name(node) -> str:
            """Return the identifier/type_identifier child text of a node, or ''."""
            for child in node.children:
                if child.type in ("identifier", "type_identifier", "name"):
                    return source_bytes[child.start_byte:child.end_byte].decode("utf-8", "ignore")
            return ""

        def walk(node, parent_class: str = "") -> None:
            if wanted and node.type in wanted:
                snippet = (
                    source_bytes[node.start_byte : node.end_byte]
                    .decode("utf-8", errors="ignore")
                    .strip()
                )
                if snippet:
                    if len(snippet) > _MAX_CHUNK_CHARS:
                        # Node is too large for the embedding model — split it.
                        for sub in _fallback.chunk(file_path, snippet):
                            sub.chunk_id = f"{node.type}-{node.start_byte}-{sub.chunk_id}"
                            sub.chunk_type = node.type
                            sub.heading = parent_class
                            sub.chunk_index = chunk_counter[0]
                            chunks.append(sub)
                            chunk_counter[0] += 1
                    else:
                        chunks.append(
                            SemanticChunk(
                                chunk_id=f"{node.type}-{node.start_byte}-{node.end_byte}",
                                chunk_type=node.type,
                                text=snippet,
                                heading=parent_class,
                                chunk_index=chunk_counter[0],
                            )
                        )
                        chunk_counter[0] += 1
                # Do not recurse into children of a matched node to avoid
                # duplicating content between parent and child chunks.
                return

            # Capture class/struct/interface name for children to inherit.
            new_parent = parent_class
            if node.type in _CLASS_CONTAINER_TYPES:
                name = _extract_name(node)
                if name:
                    new_parent = name

            for child in node.children:
                walk(child, new_parent)

        walk(tree.root_node)
        if not chunks:
            raise ValueError(f"No useful AST nodes found in {file_path!r}")
        return chunks
