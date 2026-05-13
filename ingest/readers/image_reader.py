from __future__ import annotations

import logging
from pathlib import Path

import config as cfg

from ingest.readers.base import BaseReader

logger = logging.getLogger(__name__)

from PIL import Image as _Image
import pytesseract as _pytesseract


class ImageReader(BaseReader):
    def read_content(self, path: Path) -> str:
        return self._ocr(path)

    def read_semantic(self, path: Path) -> str:
        return self._ocr(path)

    def _ocr(self, path: Path) -> str:
        try:
            lang = getattr(cfg, "TESSERACT_LANG", "eng")
            img = _Image.open(path)
            text = _pytesseract.image_to_string(img, lang=lang).strip()
            if not text:
                raise ValueError(f"No text detected in image '{path.name}'")
            return text
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError(f"Could not OCR image '{path.name}': {exc}") from exc
