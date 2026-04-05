from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SemanticChunk:
    chunk_id: str
    chunk_type: str
    text: str


class BaseChunker:
    """Abstract base for all chunking strategies."""

    def chunk(self, file_path: str, text: str) -> list[SemanticChunk]:
        raise NotImplementedError
