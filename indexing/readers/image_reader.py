from __future__ import annotations

from pathlib import Path

from indexing.readers.base import BaseReader


class ImageReader(BaseReader):
    def read_content(self, path: Path) -> str:
        try:
            from PIL import Image
            import pytesseract

            img = Image.open(path)
            return pytesseract.image_to_string(img)
        except Exception:
            return ""

    def read_sematic(self, path: Path) -> str:
        return self.read_content(path)
