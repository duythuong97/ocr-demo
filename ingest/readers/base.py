from __future__ import annotations

import io
import logging
import threading
import zipfile
from pathlib import Path

_RASTER_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif", ".gif"}

# ── PaddleOCR singleton ───────────────────────────────────────────────────────
_ocr_engine: object | None = None
_ocr_lock = threading.Lock()


def _get_ocr_engine():
    """Return a shared PaddleOCR instance (lazy-initialised, thread-safe).

    Models are downloaded to ~/.paddleocr/ on first call.
    Set OCR_LANG in .env (e.g. 'japan', 'en', 'ch', 'latin').
    """
    global _ocr_engine
    if _ocr_engine is None:
        with _ocr_lock:
            if _ocr_engine is None:
                from paddleocr import PaddleOCR  # noqa: PLC0415
                import config as _cfg  # noqa: PLC0415

                lang = getattr(_cfg, "OCR_LANG", "en")
                _ocr_engine = PaddleOCR(
                    use_angle_cls=True,
                    lang=lang,
                    use_gpu=False,
                    show_log=False,
                )
    return _ocr_engine


_log = logging.getLogger(__name__)


def _run_ocr(img) -> str:
    """Run PaddleOCR on a PIL Image or numpy array; return extracted text.

    Returns an empty string if no text is detected or PaddleOCR is not installed.
    """
    try:
        import numpy as _np  # noqa: PLC0415
        from PIL import Image as _PILImg, ImageEnhance as _Enh  # noqa: PLC0415

        engine = _get_ocr_engine()
        if isinstance(img, _PILImg.Image):
            # boost contrast for faint/compressed images
            img = img.convert("RGB")
            img = _Enh.Contrast(img).enhance(1.5)
            img = _np.array(img)
        result = engine.ocr(img, cls=True)
        _log.debug("PaddleOCR raw result: %r", result)
        if not result or not result[0]:
            _log.warning("PaddleOCR returned no results")
            return ""

        # Collect (y_center, x_center, box_height, text) using bbox coords
        # so we can reconstruct reading order (row grouping + left-to-right sort).
        boxes: list[tuple[float, float, float, str]] = []
        for line in result[0]:
            if not line or len(line) < 2:
                continue
            bbox, (text, conf) = line[0], line[1]
            if not text or conf < 0.3:
                _log.debug("Dropped low-confidence word %r (conf=%.2f)", text, conf)
                continue
            ys = [pt[1] for pt in bbox]
            xs = [pt[0] for pt in bbox]
            boxes.append((
                sum(ys) / len(ys),  # y_center
                sum(xs) / len(xs),  # x_center
                max(ys) - min(ys),  # height
                text,
            ))

        if not boxes:
            _log.warning(
                "OCR: engine ran but all boxes filtered (scores: %r)",
                [l[1][1] for l in result[0] if l and len(l) >= 2],
            )
            return ""

        # Adaptive row tolerance: 60 % of the median box height.
        median_h = sorted(b[2] for b in boxes)[len(boxes) // 2]
        row_tol = max(median_h * 0.6, 5.0)

        # Group into rows: a box joins the current row when its y_center is
        # within row_tol of the previous box's y_center.
        boxes.sort(key=lambda b: b[0])
        rows: list[list[tuple[float, float, float, str]]] = [[boxes[0]]]
        for box in boxes[1:]:
            if box[0] - rows[-1][-1][0] <= row_tol:
                rows[-1].append(box)
            else:
                rows.append([box])

        # Within each row sort left-to-right, then join rows with newlines.
        lines = ["  ".join(b[3] for b in sorted(row, key=lambda b: b[1])) for row in rows]
        extracted = "\n".join(lines).strip()
        _log.debug("OCR extracted %d rows, %d chars", len(lines), len(extracted))
        return extracted
    except Exception as exc:
        _log.warning("PaddleOCR failed: %s", exc, exc_info=True)
        return ""


def _ocr_embedded_images(path: Path, media_prefix: str) -> list[str]:
    """OCR all raster images embedded inside an Office ZIP archive (docx/pptx/xlsx).

    Opens the file as a ZIP, finds every raster image under *media_prefix*
    (e.g. ``"word/media/"``, ``"ppt/media/"``, ``"xl/media/"``), runs
    PaddleOCR on each one, and returns the non-empty extracted texts.

    Returns an empty list if PaddleOCR is not installed or the file contains
    no recognisable images.
    """
    try:
        from PIL import Image as _Img  # noqa: PLC0415
    except ImportError:
        return []

    results: list[str] = []
    try:
        with zipfile.ZipFile(path, "r") as zf:
            for name in zf.namelist():
                if not name.startswith(media_prefix):
                    continue
                if Path(name).suffix.lower() not in _RASTER_EXTS:
                    continue
                try:
                    data = zf.read(name)
                    img = _Img.open(io.BytesIO(data))
                    text = _run_ocr(img)
                    if text:
                        results.append(text)
                except Exception:
                    pass  # corrupt / unsupported image mode — skip
    except Exception:
        pass  # not a valid ZIP or path error

    return results


class BaseReader:
    def read_content(self, path: Path) -> str:
        raise NotImplementedError

    def read_semantic(self, path: Path) -> str:
        return self.read_content(path)
