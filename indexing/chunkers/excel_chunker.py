from __future__ import annotations

from indexing.chunkers.base import BaseChunker, SemanticChunk
from indexing.chunkers.text_chunker import TextChunker

# Fallback chunker for sections that exceed the size threshold
_fallback = TextChunker(chunk_chars=1400, overlap=200)


class ExcelChunker(BaseChunker):
    """Structure-aware chunker for Excel files.

    Expects *text* produced by ``ExcelReader.read_sematic()`` which uses
    ``excel_to_rag_text.convert_workbook(row_sentences=True)``.

    The text is already structured as::

        [Sheet: sheetname]

        ### Section heading
        header1: val1 / header2: val2 / ...
        header1: val1 / header2: val2 / ...

        ### Next section
        ...

    Strategy:
    - Split on ``[Sheet: ...]`` and ``### `` markers → one chunk per section.
    - If a section is larger than *max_section_chars*, fall back to sliding-
      window chunking so no vector is fed an oversized context.
    """

    def __init__(self, max_section_chars: int = 2000) -> None:
        self.max_section_chars = max_section_chars

    def chunk(self, file_path: str, text: str) -> list[SemanticChunk]:
        chunks: list[SemanticChunk] = []
        chunk_index = 0
        current_sheet = ""
        current_heading = ""
        current_lines: list[str] = []

        def flush():
            nonlocal chunk_index
            body = "\n".join(current_lines).strip()
            if not body:
                return
            prefix = ""
            if current_sheet:
                prefix += f"[Sheet: {current_sheet}] "
            if current_heading:
                prefix += f"{current_heading}\n"
            full_text = (prefix + body).strip()
            if len(full_text) <= self.max_section_chars:
                chunks.append(
                    SemanticChunk(
                        chunk_id=f"chunk-{chunk_index}",
                        chunk_type="excel_section",
                        text=full_text,
                    )
                )
                chunk_index += 1
            else:
                # Section too large — slide-window split, prefix every sub-chunk
                for sub in _fallback.chunk(file_path, full_text):
                    sub.chunk_id = f"chunk-{chunk_index}"
                    sub.chunk_type = "excel_section"
                    chunks.append(sub)
                    chunk_index += 1

        for line in text.splitlines():
            if line.startswith("[Sheet: ") and line.endswith("]"):
                flush()
                current_lines = []
                current_sheet = line[8:-1]
                current_heading = ""
            elif line.startswith("### "):
                flush()
                current_lines = []
                current_heading = line[4:].strip()
            else:
                current_lines.append(line)

        flush()
        return chunks
