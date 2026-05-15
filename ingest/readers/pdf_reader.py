from __future__ import annotations

import logging
from pathlib import Path
import config as cfg
from ingest.readers.base import BaseReader, _run_ocr
from pypdf import PdfReader as PdfFileReader

try:
    from pdf2image import convert_from_path as _convert_from_path
    _OCR_AVAILABLE = True
except ImportError:
    _OCR_AVAILABLE = False

logger = logging.getLogger(__name__)


class PdfReader(BaseReader):
    def read_content(self, path: Path) -> str:
        return self._extract(path)

    def read_semantic(self, path: Path) -> str:
        return self._extract(path)

    def _extract(self, path: Path) -> str:
        try:
            reader = PdfFileReader(str(path))
            pages_text: list[str] = []
            scanned_pages: list[int] = []

            for i, page in enumerate(reader.pages):
                text = (page.extract_text() or "").strip()
                if text:
                    pages_text.append(text)
                else:
                    scanned_pages.append(i)

            # OCR fallback for pages with no extractable text (scanned pages)
            if scanned_pages:
                if getattr(cfg, "PDF_OCR_FALLBACK", True):
                    ocr_parts = self._ocr_pages(path, scanned_pages)
                    pages_text.extend(ocr_parts)

            result = "\n".join(pages_text).strip()
            if not result:
                raise ValueError(f"No extractable text content in '{path.name}'")
            return result
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError(f"Could not read PDF file '{path.name}': {exc}") from exc

    def _ocr_pages(self, path: Path, page_indices: list[int]) -> list[str]:
        """Render specified pages to images and run PaddleOCR."""
        if not _OCR_AVAILABLE:
            raise ImportError(
                "pdf2image must be installed for PDF OCR. "
                "Run: pip install pdf2image"
            )
        dpi = 200
        results: list[str] = []

        # convert_from_path uses 1-based page numbers
        all_images = _convert_from_path(
            str(path),
            dpi=dpi,
            first_page=min(page_indices) + 1,
            last_page=max(page_indices) + 1,
        )

        # Align images back to page_indices
        min_idx = min(page_indices)
        for img_offset, page_idx in enumerate(range(min_idx, max(page_indices) + 1)):
            if page_idx not in page_indices:
                continue
            if img_offset >= len(all_images):
                break
            text = _run_ocr(all_images[img_offset])
            if text:
                results.append(text)
        return results
