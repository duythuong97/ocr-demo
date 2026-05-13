from __future__ import annotations

from pathlib import Path

from ingest.chunkers.base import BaseChunker, SemanticChunk
from ingest.chunkers.excel_chunker import ExcelChunker
from ingest.chunkers.pptx_chunker import PptxChunker
from ingest.chunkers.text_chunker import TextChunker
from ingest.chunkers.tree_sitter_code_chunker import TreeSitterCodeChunker
from ingest.chunkers.word_chunker import WordChunker

# Shared instances (lazy-initialised parsers are cached inside each chunker)
_code = TreeSitterCodeChunker()
_text = TextChunker(chunk_chars=1800, overlap=220)
_excel = ExcelChunker()
_word = WordChunker()
_pptx = PptxChunker()

_EXCEL_EXTENSIONS = {".xlsx", ".xlsm", ".xlam", ".xls", ".csv"}
_WORD_EXTENSIONS = {".docx"}
_PPTX_EXTENSIONS = {".pptx", ".pptm"}

# Extensions handled by TreeSitterCodeChunker (no fallback)
_CODE_EXTENSIONS = {
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".java",
    ".go",
    ".sql",
    ".sh",
    ".rb",
    ".kt",
    ".rs",
    ".cpp",
    ".c",
    ".h",
    ".hpp",
    ".cs",
}


def get_chunker(file_path: str) -> BaseChunker:
    """Return the appropriate chunker for *file_path* based on extension."""
    ext = Path(file_path).suffix.lower()
    if ext in _CODE_EXTENSIONS:
        return _code
    if ext in _EXCEL_EXTENSIONS:
        return _excel
    if ext in _WORD_EXTENSIONS:
        return _word
    if ext in _PPTX_EXTENSIONS:
        return _pptx
    return _text


def chunk_for_semantic(file_path: str, text: str) -> list[SemanticChunk]:
    """Convenience wrapper: select chunker and return chunks for *file_path*.

    If the primary chunker raises ValueError (e.g. TreeSitter finds no AST
    nodes in an empty/stub file), falls back to the text chunker so the file
    is indexed as plain text rather than failing entirely.
    """
    chunker = get_chunker(file_path)
    try:
        return chunker.chunk(file_path, text)
    except ValueError:
        if chunker is not _text:
            import logging
            logging.getLogger(__name__).debug(
                "Primary chunker %s failed for %s, falling back to TextChunker",
                type(chunker).__name__,
                file_path,
            )
            return _text.chunk(file_path, text)
        raise
