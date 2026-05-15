from __future__ import annotations

import logging
from pathlib import Path

from ingest.readers.base import BaseReader, _run_ocr

logger = logging.getLogger(__name__)


class ImageReader(BaseReader):
    def read_content(self, path: Path) -> str:
        return self._ocr(path)

    def read_semantic(self, path: Path) -> str:
        return self._ocr(path)

    def _ocr(self, path: Path) -> str:
        try:
            from PIL import Image as _Image  # noqa: PLC0415
            img = _Image.open(path)
            text = _run_ocr(img)
            if not text:
                raise ValueError(f"No text detected in image '{path.name}'")
            return text
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError(f"Could not OCR image '{path.name}': {exc}") from exc
