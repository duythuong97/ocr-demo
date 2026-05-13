from __future__ import annotations

from pathlib import Path

from ingest.readers.base import BaseReader
from ingest.readers.encoding_utils import read_text_auto


class TextReader(BaseReader):
    def read_content(self, path: Path) -> str:
        return read_text_auto(path)

    def read_semantic(self, path: Path) -> str:
        return self.read_content(path)
