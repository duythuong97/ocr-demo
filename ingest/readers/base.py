from __future__ import annotations

from pathlib import Path


class BaseReader:
    def read_content(self, path: Path) -> str:
        raise NotImplementedError

    def read_semantic(self, path: Path) -> str:
        return self.read_content(path)
