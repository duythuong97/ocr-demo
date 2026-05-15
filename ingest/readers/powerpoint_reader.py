from __future__ import annotations

import logging
import re
import subprocess
import tempfile
from pathlib import Path

from ingest.readers.base import BaseReader, _ocr_embedded_images

logger = logging.getLogger(__name__)

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE


def _check_encrypted(path: Path) -> None:
    """Raise ValueError if the Office file is password-protected."""
    try:
        import msoffcrypto

        with open(path, "rb") as f:
            office_file = msoffcrypto.OfficeFile(f)
            if office_file.is_encrypted():
                raise ValueError(f"File is password-protected: {path.name}")
    except ImportError:
        pass  # msoffcrypto not installed — skip check
    except ValueError:
        raise
    except Exception as exc:
        logger.debug("Unexpected error checking encryption for %s: %s", path.name, exc)


class PowerPointReader(BaseReader):
    """Reader for PowerPoint files.

    Supported formats:
      - .pptx / .pptm  — via python-pptx
      - .ppt            — legacy binary format; attempts extraction via libreoffice
                          or falls back to a best-effort raw text extraction.

    Extracts:
      - Slide text frames
      - Table cell text
      - Speaker notes

    Raises ValueError for password-protected files.
    """

    def read_content(self, path: Path) -> str:
        ext = path.suffix.lower()
        if ext in {".pptx", ".pptm"}:
            return self._read_pptx(path)
        # .ppt — legacy binary
        return self._read_ppt_legacy(path)

    def read_semantic(self, path: Path) -> str:
        return self.read_content(path)

    # ------------------------------------------------------------------

    def _read_pptx(self, path: Path) -> str:
        _check_encrypted(path)
        try:
            prs = Presentation(str(path))
            parts: list[str] = []

            for slide_num, slide in enumerate(prs.slides, start=1):
                # Extract slide title from title/subtitle placeholder shapes first
                slide_title = ""
                for shape in slide.shapes:
                    if (
                        shape.has_text_frame
                        and shape.shape_type != MSO_SHAPE_TYPE.GROUP
                    ):
                        ph = getattr(shape, "placeholder_format", None)
                        if ph is not None and ph.idx in (0, 1):  # 0=title, 1=subtitle
                            t = shape.text_frame.text.strip()
                            if t:
                                slide_title = t
                                break
                if slide_title:
                    parts.append(f"[Slide {slide_num} - {slide_title}]")
                else:
                    parts.append(f"[Slide {slide_num}]")
                self._extract_shapes(slide.shapes, parts)

                # Speaker notes
                if slide.has_notes_slide:
                    notes_text = slide.notes_slide.notes_text_frame.text.strip()
                    if notes_text:
                        parts.append(f"[Notes] {notes_text}")

            # Embedded images (diagrams, screenshots, scanned content)
            img_parts = _ocr_embedded_images(path, "ppt/media/")
            if img_parts:
                parts.append("### Embedded Images")
                parts.extend(img_parts)

            return "\n".join(parts)
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError(
                f"Could not read PowerPoint file '{path.name}': {exc}"
            ) from exc

    def _extract_shapes(self, shapes, parts: list[str]) -> None:
        """Recursively extract text from shapes, including grouped shapes."""
        for shape in shapes:
            # Grouped shapes — recurse
            if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
                self._extract_shapes(shape.shapes, parts)
                continue

            # Text frames (titles, content boxes, text boxes)
            if shape.has_text_frame:
                for para in shape.text_frame.paragraphs:
                    text = para.text.strip()
                    if text:
                        parts.append(text)

            # Table cells
            if shape.has_table:
                for row in shape.table.rows:
                    cells = [c.text.strip() for c in row.cells if c.text.strip()]
                    if cells:
                        parts.append(" | ".join(cells))

            # Chart titles and axis labels
            try:
                if shape.has_chart:
                    chart = shape.chart
                    if chart.has_title and chart.chart_title.has_text_frame:
                        title = chart.chart_title.text_frame.text.strip()
                        if title:
                            parts.append(f"[Chart] {title}")
                    # Category/series labels
                    try:
                        for series in chart.series:
                            if series.name:
                                parts.append(series.name)
                    except Exception as exc:
                        logger.debug("Could not read chart series: %s", exc)
            except Exception as exc:
                logger.debug("Could not read chart from shape: %s", exc)

            # SmartArt, diagrams, and other graphic frames not exposed by python-pptx
            # (has_text_frame=False, has_table=False, has_chart=False)
            if not shape.has_text_frame and not shape.has_table:
                try:
                    A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
                    seen: set[str] = set()
                    for t_elem in shape.element.iter(f"{{{A_NS}}}t"):
                        txt = (t_elem.text or "").strip()
                        if txt and txt not in seen:
                            seen.add(txt)
                            parts.append(txt)
                except Exception as exc:
                    logger.debug("Could not read graphic frame text: %s", exc)

    def _read_ppt_legacy(self, path: Path) -> str:
        """Best-effort extraction for legacy .ppt binary format.

        Attempts conversion via LibreOffice headless → .pptx, then falls back
        to raw binary string extraction.
        """
        _check_encrypted(path)

        # Try LibreOffice conversion first
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                result = subprocess.run(
                    [
                        "libreoffice",
                        "--headless",
                        "--convert-to",
                        "pptx",
                        "--outdir",
                        tmpdir,
                        str(path),
                    ],
                    capture_output=True,
                    timeout=60,
                )
                if result.returncode == 0:
                    converted = list(Path(tmpdir).glob("*.pptx"))
                    if converted:
                        return self._read_pptx(converted[0])
        except FileNotFoundError:
            logger.debug(
                "LibreOffice not found — falling back to binary extraction for %s",
                path.name,
            )
        except subprocess.TimeoutExpired:
            logger.warning(
                "LibreOffice conversion timed out for %s — falling back to binary extraction",
                path.name,
            )
        except Exception as exc:
            logger.warning(
                "LibreOffice conversion failed for %s: %s — falling back to binary extraction",
                path.name,
                exc,
            )

        # Raw binary extraction fallback
        try:
            raw = path.read_bytes()
            utf16_strings = re.findall(rb"(?:[\x20-\x7e]\x00){4,}", raw)
            decoded = [
                s.decode("utf-16-le", errors="ignore").strip() for s in utf16_strings
            ]

            ascii_strings = re.findall(rb"[\x20-\x7e]{4,}", raw)
            decoded += [
                s.decode("ascii", errors="ignore").strip() for s in ascii_strings
            ]

            seen: set[str] = set()
            result_lines: list[str] = []
            for line in decoded:
                if line and line not in seen:
                    seen.add(line)
                    result_lines.append(line)

            return "\n".join(result_lines)
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError(
                f"Could not read legacy PPT file '{path.name}': {exc}"
            ) from exc
