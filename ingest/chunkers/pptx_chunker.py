from __future__ import annotations

import re

from ingest.chunkers.base import BaseChunker, SemanticChunk
from ingest.chunkers.text_chunker import TextChunker

_fallback = TextChunker(chunk_chars=1400, overlap=200)


class PptxChunker(BaseChunker):
    """Structure-aware chunker for PowerPoint files (.pptx/.pptm).

    Expects *text* produced by ``PowerPointReader.read_content()`` /
    ``read_semantic()`` which emits ``[Slide N]`` markers.

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
            # Extract slide number from marker like "[Slide 3 - Architecture Overview]"
            _slide_num = 0
            _slide_title = ""
            if current_slide.startswith("[Slide "):
                _m = re.match(r'\[Slide (\d+)(?:\s+-\s+(.+))?\]', current_slide)
                if _m:
                    _slide_num = int(_m.group(1))
                    _slide_title = (_m.group(2) or "").strip()
            if len(full_text) <= self.max_slide_chars:
                chunks.append(
                    SemanticChunk(
                        chunk_id=f"chunk-{chunk_index}",
                        chunk_type="pptx_slide",
                        text=full_text,
                        heading=_slide_title,
                        page_num=_slide_num,
                        chunk_index=chunk_index,
                    )
                )
                chunk_index += 1
            else:
                for sub in _fallback.chunk(file_path, full_text):
                    sub.chunk_id = f"chunk-{chunk_index}"
                    sub.chunk_type = "pptx_slide"
                    sub.heading = _slide_title
                    sub.page_num = _slide_num
                    sub.chunk_index = chunk_index
                    # Ensure slide marker is present in every sub-chunk text
                    if current_slide and not sub.text.startswith(current_slide):
                        sub.text = f"{current_slide}\n{sub.text}"
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
