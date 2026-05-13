from __future__ import annotations

from ingest.chunkers.base import BaseChunker, SemanticChunk


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
        # Tolerance window (chars) to search backwards for a sentence boundary
        _boundary_window = min(200, self.chunk_chars // 4)
        # Include Japanese/full-width sentence terminators (。！？)
        _sentence_ends = {".", "!", "?", "\n", "。", "！", "？"}

        while start < text_len:
            end = min(text_len, start + self.chunk_chars)
            # Walk back within tolerance to snap to a sentence boundary
            if end < text_len:
                snap = end
                for i in range(end, max(end - _boundary_window, start), -1):
                    if text[i - 1] in _sentence_ends:
                        snap = i
                        break
                if snap > start:
                    end = snap
            piece = text[start:end].strip()
            if piece:
                chunks.append(
                    SemanticChunk(
                        chunk_id=f"chunk-{index}",
                        chunk_type="text",
                        text=piece,
                        chunk_index=index,
                    )
                )
                index += 1
            if end >= text_len:
                break
            start = max(0, end - self.overlap)
        return chunks
