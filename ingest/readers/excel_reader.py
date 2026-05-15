from __future__ import annotations

import csv
import logging
from pathlib import Path

from ingest.readers.base import BaseReader, _ocr_embedded_images
from ingest.readers.encoding_utils import decode_bytes_auto

logger = logging.getLogger(__name__)

import openpyxl
from ingest.excel_parser import convert_workbook

try:
    import xlrd
    _XLRD_AVAILABLE = True
except ImportError:
    _XLRD_AVAILABLE = False


def _check_encrypted(path: Path) -> None:
    """Raise ValueError if the Office file is password-protected."""
    try:
        import msoffcrypto

        with open(path, "rb") as f:
            office_file = msoffcrypto.OfficeFile(f)
            if office_file.is_encrypted():
                raise ValueError(f"File is password-protected: {path.name}")
    except ImportError:
        pass  # msoffcrypto not installed — attempt open and let the library report
    except ValueError:
        raise
    except Exception:
        raise


class ExcelReader(BaseReader):
    """Reader for Excel and CSV files.

    Supported formats:
      - .xlsx / .xlsm / .xlam — via openpyxl + indexing.excel_parser pipeline
      - .xls                  — via xlrd (old binary format)
      - .csv                  — stdlib csv module

    For .xlsx files two outputs are produced:
      read_content()  — Markdown-table format for Solr fulltext search.
      read_semantic()  — Row-sentence format (forward-filled merged cells) for
                        dense-vector embeddings in Qdrant.

    Password-protected files raise ValueError so the indexing worker records
    a clear error message without crashing.
    """

    def read_content(self, path: Path) -> str:
        ext = path.suffix.lower()
        if ext == ".csv":
            return self._read_csv(path)
        if ext == ".xls":
            return self._read_xls(path)
        # .xlsx / .xlsm / .xlam — Markdown tables for Solr
        return self._read_xlsx(path, row_sentences=False)

    def read_semantic(self, path: Path) -> str:
        ext = path.suffix.lower()
        if ext in {".csv", ".xls"}:
            return self.read_content(path)
        # .xlsx / .xlsm / .xlam — sentence-per-row for Qdrant embeddings
        return self._read_xlsx(path, row_sentences=True)

    # ------------------------------------------------------------------

    def _read_xlsx(self, path: Path, row_sentences: bool = False) -> str:
        _check_encrypted(path)
        try:
            # load_workbook does not support read_only=True when merge_map is
            # needed (merged_cells are unavailable in read-only mode).
            wb = openpyxl.load_workbook(str(path), data_only=True)
            text = convert_workbook(wb, row_sentences=row_sentences)
            wb.close()
            # Embedded images (charts, diagrams pasted as pictures)
            img_parts = _ocr_embedded_images(path, "xl/media/")
            if img_parts:
                text += "\n### Embedded Images\n" + "\n".join(img_parts)
            return text
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError(f"Could not read Excel file '{path.name}': {exc}") from exc

    def _read_xls(self, path: Path) -> str:
        if not _XLRD_AVAILABLE:
            raise ValueError(
                f"xlrd not installed — cannot read '{path.name}'. "
                "Run: pip install xlrd"
            )
        try:
            wb = xlrd.open_workbook(str(path))
            parts: list[str] = []
            for sheet in wb.sheets():
                parts.append(f"[Sheet: {sheet.name}]")
                for rx in range(sheet.nrows):
                    cells = [
                        str(sheet.cell_value(rx, cx))
                        for cx in range(sheet.ncols)
                        if str(sheet.cell_value(rx, cx)).strip()
                    ]
                    if cells:
                        parts.append(" | ".join(cells))
            return "\n".join(parts)
        except Exception as exc:
            msg = str(exc).lower()
            if any(
                kw in msg for kw in ("encrypted", "password", "workbook key", "biff")
            ):
                raise ValueError(f"File is password-protected: {path.name}") from exc
            raise ValueError(f"Could not read XLS file '{path.name}': {exc}") from exc

    def _read_csv(self, path: Path) -> str:
        try:
            rows: list[str] = []
            raw_bytes = path.read_bytes()
            text_content = decode_bytes_auto(raw_bytes, str(path))
            import io
            with io.StringIO(text_content, newline=None) as f:
                reader = csv.reader(f)
                for row in reader:
                    line = " | ".join(c.strip() for c in row if c.strip())
                    if line:
                        rows.append(line)
            return "\n".join(rows)
        except Exception as exc:
            raise ValueError(f"Could not read CSV file '{path.name}': {exc}") from exc
