from __future__ import annotations

from pathlib import Path

from indexing.readers.base import BaseReader


class TextReader(BaseReader):
    def read_content(self, path: Path) -> str:
        return path.read_text(encoding="utf-8", errors="ignore")

    def read_sematic(self, path: Path) -> str:
        return self.read_content(path)
