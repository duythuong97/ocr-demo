from __future__ import annotations

from indexing.chunkers.base import BaseChunker, SemanticChunk


class TextChunker(BaseChunker):
    """Sliding-window character chunker for plain text and generic code."""

    def __init__(self, chunk_chars: int = 1400, overlap: int = 200) -> None:
        self.chunk_chars = chunk_chars
        self.overlap = overlap

    def chunk(self, file_path: str, text: str) -> list[SemanticChunk]:
        chunks: list[SemanticChunk] = []
        start = 0
        index = 0
        text_len = len(text)
        while start < text_len:
            end = min(text_len, start + self.chunk_chars)
            piece = text[start:end].strip()
            if piece:
                chunks.append(
                    SemanticChunk(
                        chunk_id=f"chunk-{index}",
                        chunk_type="text",
                        text=piece,
                    )
                )
                index += 1
            if end >= text_len:
                break
            start = max(0, end - self.overlap)
        return chunks
