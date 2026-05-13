from __future__ import annotations

from ingest.chunkers.base import BaseChunker, SemanticChunk
from ingest.chunkers.text_chunker import TextChunker

# Fallback chunker for sections that exceed the size threshold
_fallback = TextChunker(chunk_chars=1400, overlap=200)


class ExcelChunker(BaseChunker):
    """Structure-aware chunker for Excel files.

    Expects *text* produced by ``ExcelReader.read_semantic()`` which uses
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

    def __init__(self, max_section_chars: int = 2000, min_body_chars: int = 20) -> None:
        self.max_section_chars = max_section_chars
        self.min_body_chars = min_body_chars

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
            # Skip chunks whose body is too short to be meaningful
            # (e.g. a sheet title cell that just repeats the sheet name)
            if len(body) < self.min_body_chars and body == current_sheet:
                return
            if len(body) < self.min_body_chars and not current_heading:
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
                        heading=current_heading,
                        sheet_name=current_sheet,
                        chunk_index=chunk_index,
                    )
                )
                chunk_index += 1
            else:
                # Section too large — slide-window split, prefix every sub-chunk
                for sub in _fallback.chunk(file_path, full_text):
                    sub.chunk_id = f"chunk-{chunk_index}"
                    sub.chunk_type = "excel_section"
                    sub.heading = current_heading
                    sub.sheet_name = current_sheet
                    sub.chunk_index = chunk_index
                    # Ensure prefix context is present in every sub-chunk text
                    if prefix and not sub.text.startswith(prefix.strip()):
                        sub.text = f"{prefix.strip()}\n{sub.text}"
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
