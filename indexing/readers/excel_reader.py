from __future__ import annotations

import csv
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


class ExcelReader(BaseReader):
    """Reader for Excel and CSV files.

    Supported formats:
      - .xlsx / .xlsm / .xlam — via openpyxl + indexing.excel_parser pipeline
      - .xls                  — via xlrd (old binary format)
      - .csv                  — stdlib csv module

    For .xlsx files two outputs are produced:
      read_content()  — Markdown-table format for Solr fulltext search.
      read_sematic()  — Row-sentence format (forward-filled merged cells) for
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

    def read_sematic(self, path: Path) -> str:
        ext = path.suffix.lower()
        if ext in {".csv", ".xls"}:
            return self.read_content(path)
        # .xlsx / .xlsm / .xlam — sentence-per-row for Qdrant embeddings
        return self._read_xlsx(path, row_sentences=True)

    # ------------------------------------------------------------------

    def _read_xlsx(self, path: Path, row_sentences: bool = False) -> str:
        _check_encrypted(path)
        try:
            import openpyxl
            from indexing.excel_parser import convert_workbook

            # load_workbook does not support read_only=True when merge_map is
            # needed (merged_cells are unavailable in read-only mode).
            wb = openpyxl.load_workbook(str(path), data_only=True)
            text = convert_workbook(wb, row_sentences=row_sentences)
            wb.close()
            return text
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError(f"Could not read Excel file '{path.name}': {exc}") from exc

    def _read_xls(self, path: Path) -> str:
        try:
            import xlrd

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
            if any(kw in msg for kw in ("encrypted", "password", "workbook key", "biff")):
                raise ValueError(f"File is password-protected: {path.name}") from exc
            raise ValueError(f"Could not read XLS file '{path.name}': {exc}") from exc

    def _read_csv(self, path: Path) -> str:
        try:
            rows: list[str] = []
            with open(path, encoding="utf-8", errors="ignore", newline="") as f:
                reader = csv.reader(f)
                for row in reader:
                    line = " | ".join(c.strip() for c in row if c.strip())
                    if line:
                        rows.append(line)
            return "\n".join(rows)
        except Exception as exc:
            raise ValueError(f"Could not read CSV file '{path.name}': {exc}") from exc
