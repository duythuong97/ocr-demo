from __future__ import annotations

from pathlib import Path

from indexing.readers.base import BaseReader


def _check_encrypted(path: Path) -> None:
    """Raise ValueError if the Office file is password-protected."""
    try:
        import msoffcrypto

        with open(path, "rb") as f:
            office_file = msoffcrypto.OfficeFile(f)
            if office_file.is_encrypted():
                raise ValueError(f"File is password-protected: {path.name}")
    except ImportError:
        pass
    except ValueError:
        raise
    except Exception:
        pass


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

    def read_sematic(self, path: Path) -> str:
        return self.read_content(path)

    # ------------------------------------------------------------------

    def _read_pptx(self, path: Path) -> str:
        _check_encrypted(path)
        try:
            from pptx import Presentation
            from pptx.util import Pt

            prs = Presentation(str(path))
            parts: list[str] = []

            for slide_num, slide in enumerate(prs.slides, start=1):
                parts.append(f"[Slide {slide_num}]")

                for shape in slide.shapes:
                    # Text frames (titles, content boxes)
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

                # Speaker notes
                if slide.has_notes_slide:
                    notes_text = slide.notes_slide.notes_text_frame.text.strip()
                    if notes_text:
                        parts.append(f"[Notes] {notes_text}")

            return "\n".join(parts)
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError(
                f"Could not read PowerPoint file '{path.name}': {exc}"
            ) from exc

    def _read_ppt_legacy(self, path: Path) -> str:
        """Best-effort extraction for legacy .ppt binary format.

        python-pptx does not support .ppt.  We try to extract readable
        strings from the raw binary as a fallback — sufficient for search
        indexing even if formatting is lost.
        """
        _check_encrypted(path)
        try:
            raw = path.read_bytes()
            # Extract ASCII/UTF-16-LE printable strings ≥ 4 chars from the binary
            import re

            # UTF-16-LE strings (common in Office binary formats)
            utf16_strings = re.findall(
                rb"(?:[\x20-\x7e]\x00){4,}", raw
            )
            decoded = [s.decode("utf-16-le", errors="ignore").strip() for s in utf16_strings]

            # ASCII fallback
            ascii_strings = re.findall(rb"[\x20-\x7e]{4,}", raw)
            decoded += [s.decode("ascii", errors="ignore").strip() for s in ascii_strings]

            # Deduplicate while preserving order
            seen: set[str] = set()
            result: list[str] = []
            for line in decoded:
                if line and line not in seen:
                    seen.add(line)
                    result.append(line)

            return "\n".join(result)
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError(
                f"Could not read legacy PPT file '{path.name}': {exc}"
            ) from exc
