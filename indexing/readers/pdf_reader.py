from __future__ import annotations

from pathlib import Path

from indexing.readers.base import BaseReader


class PdfReader(BaseReader):
    def read_content(self, path: Path) -> str:
        try:
            from pypdf import PdfReader as PdfFileReader

            reader = PdfFileReader(str(path))
            return "\n".join((p.extract_text() or "") for p in reader.pages)
        except Exception:
            return ""

    def read_sematic(self, path: Path) -> str:
        return self.read_content(path)
