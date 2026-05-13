from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SemanticChunk:
    chunk_id: str
    chunk_type: str
    text: str
    # ── Structural metadata (populated by structure-aware chunkers) ──────────
    heading: str = ""          # Section/heading title (Word, Excel section)
    sheet_name: str = ""       # Excel sheet name
    page_num: int = 0          # PDF page number (1-based) or slide number
    chunk_index: int = 0       # Sequential index within the file (for neighbor retrieval)


class BaseChunker:
    """Abstract base for all chunking strategies."""

    def chunk(self, file_path: str, text: str) -> list[SemanticChunk]:
        raise NotImplementedError
