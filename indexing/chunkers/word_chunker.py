from __future__ import annotations

from indexing.chunkers.base import BaseChunker, SemanticChunk
from indexing.chunkers.text_chunker import TextChunker

_fallback = TextChunker(chunk_chars=1400, overlap=200)


class WordChunker(BaseChunker):
    """Structure-aware chunker for Word documents (.docx).

    Expects *text* produced by ``WordReader.read_sematic()`` which emits
    ``### Heading`` markers before each section.

    Strategy:
    - Split on ``### `` markers → one chunk per heading section.
    - If a section exceeds *max_section_chars*, fall back to sliding-window.
    """

    def __init__(self, max_section_chars: int = 2000) -> None:
        self.max_section_chars = max_section_chars

    def chunk(self, file_path: str, text: str) -> list[SemanticChunk]:
        chunks: list[SemanticChunk] = []
        chunk_index = 0
        current_heading = ""
        current_lines: list[str] = []

        def flush():
            nonlocal chunk_index
            body = "\n".join(current_lines).strip()
            if not body:
                return
            full_text = (f"{current_heading}\n{body}".strip()) if current_heading else body
            if len(full_text) <= self.max_section_chars:
                chunks.append(
                    SemanticChunk(
                        chunk_id=f"chunk-{chunk_index}",
                        chunk_type="word_section",
                        text=full_text,
                    )
                )
                chunk_index += 1
            else:
                for sub in _fallback.chunk(file_path, full_text):
                    sub.chunk_id = f"chunk-{chunk_index}"
                    sub.chunk_type = "word_section"
                    chunks.append(sub)
                    chunk_index += 1

        for line in text.splitlines():
            if line.startswith("### "):
                flush()
                current_lines = []
                current_heading = line[4:].strip()
            else:
                current_lines.append(line)

        flush()
        return chunks
