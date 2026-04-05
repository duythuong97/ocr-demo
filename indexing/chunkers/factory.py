from __future__ import annotations

from pathlib import Path

from indexing.chunkers.base import BaseChunker, SemanticChunk
from indexing.chunkers.csharp_chunker import CSharpChunker
from indexing.chunkers.excel_chunker import ExcelChunker
from indexing.chunkers.pptx_chunker import PptxChunker
from indexing.chunkers.text_chunker import TextChunker
from indexing.chunkers.tree_sitter_code_chunker import TreeSitterCodeChunker
from indexing.chunkers.word_chunker import WordChunker

# Shared instances (lazy-initialised parsers are cached inside each chunker)
_csharp = CSharpChunker()
_code = TreeSitterCodeChunker(fallback=TextChunker(chunk_chars=1200, overlap=180))
_text = TextChunker(chunk_chars=1800, overlap=220)
_excel = ExcelChunker()
_word = WordChunker()
_pptx = PptxChunker()

_EXCEL_EXTENSIONS = {".xlsx", ".xlsm", ".xlam", ".xls", ".csv"}
_WORD_EXTENSIONS = {".docx"}
_PPTX_EXTENSIONS = {".pptx", ".pptm"}

# Extensions that use the tighter code chunker instead of the wide text chunker
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
    ".swift",
    ".rs",
    ".cpp",
    ".c",
    ".h",
    ".hpp",
}


def get_chunker(file_path: str) -> BaseChunker:
    """Return the appropriate chunker for *file_path* based on extension."""
    ext = Path(file_path).suffix.lower()
    if ext == ".cs":
        return _csharp
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
    """Convenience wrapper: select chunker and return chunks for *file_path*."""
    return get_chunker(file_path).chunk(file_path, text)
