from __future__ import annotations

from indexing.chunkers.base import BaseChunker, SemanticChunk
from indexing.chunkers.text_chunker import TextChunker

_fallback = TextChunker(chunk_chars=1400, overlap=200)


class PptxChunker(BaseChunker):
    """Structure-aware chunker for PowerPoint files (.pptx/.pptm).

    Expects *text* produced by ``PowerPointReader.read_content()`` /
    ``read_sematic()`` which emits ``[Slide N]`` markers.

    Strategy:
    - One chunk per slide.
    - If a slide exceeds *max_slide_chars*, fall back to sliding-window.
    """

    def __init__(self, max_slide_chars: int = 2000) -> None:
        self.max_slide_chars = max_slide_chars

    def chunk(self, file_path: str, text: str) -> list[SemanticChunk]:
        chunks: list[SemanticChunk] = []
        chunk_index = 0
        current_slide = ""
        current_lines: list[str] = []

        def flush():
            nonlocal chunk_index
            body = "\n".join(current_lines).strip()
            if not body:
                return
            full_text = (f"{current_slide}\n{body}".strip()) if current_slide else body
            if len(full_text) <= self.max_slide_chars:
                chunks.append(
                    SemanticChunk(
                        chunk_id=f"chunk-{chunk_index}",
                        chunk_type="pptx_slide",
                        text=full_text,
                    )
                )
                chunk_index += 1
            else:
                for sub in _fallback.chunk(file_path, full_text):
                    sub.chunk_id = f"chunk-{chunk_index}"
                    sub.chunk_type = "pptx_slide"
                    chunks.append(sub)
                    chunk_index += 1

        for line in text.splitlines():
            if line.startswith("[Slide ") and line.endswith("]"):
                flush()
                current_lines = []
                current_slide = line
            else:
                current_lines.append(line)

        flush()
        return chunks
