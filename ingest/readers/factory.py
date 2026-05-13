from __future__ import annotations

from pathlib import Path

from ingest.readers.base import BaseReader
from ingest.readers.default_reader import DefaultReader
from ingest.readers.excel_reader import ExcelReader
from ingest.readers.html_reader import HtmlReader
from ingest.readers.image_reader import ImageReader
from ingest.readers.pdf_reader import PdfReader
from ingest.readers.powerpoint_reader import PowerPointReader
from ingest.readers.text_reader import TextReader
from ingest.readers.word_reader import WordReader
from ingest.readers.xml_reader import XmlReader

TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".rst",
    ".json",
    ".yaml",
    ".yml",
    ".cs",
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".java",
    ".go",
    ".sql",
    ".sh",
}

# Markup/template files — strip tags, skip <script>/<style> content
HTML_EXTENSIONS = {
    ".html",
    ".htm",
    ".vue",  # Vue SFC: strips <script>/<style>, keeps <template> text
    ".cshtml",  # ASP.NET Razor
    ".razor",  # Blazor / Razor components
    ".svg",  # SVG: strips XML tags, extracts <text>/<title> content
}

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".webp"}

WORD_EXTENSIONS = {".docx"}

EXCEL_EXTENSIONS = {".xlsx", ".xlsm", ".xlam", ".xls", ".csv"}

POWERPOINT_EXTENSIONS = {".pptx", ".pptm", ".ppt"}


class ReaderFactory:
    def __init__(self):
        self._text_reader = TextReader()
        self._html_reader = HtmlReader()
        self._xml_reader = XmlReader()
        self._pdf_reader = PdfReader()
        self._image_reader = ImageReader()
        self._word_reader = WordReader()
        self._excel_reader = ExcelReader()
        self._powerpoint_reader = PowerPointReader()
        self._default_reader = DefaultReader()

    def get_reader(self, path: Path) -> BaseReader:
        ext = path.suffix.lower()
        if ext == ".xml":
            return self._xml_reader
        if ext in HTML_EXTENSIONS:
            return self._html_reader
        if ext in TEXT_EXTENSIONS:
            return self._text_reader
        if ext == ".pdf":
            return self._pdf_reader
        if ext in IMAGE_EXTENSIONS:
            return self._image_reader
        if ext in WORD_EXTENSIONS:
            return self._word_reader
        if ext in EXCEL_EXTENSIONS:
            return self._excel_reader
        if ext in POWERPOINT_EXTENSIONS:
            return self._powerpoint_reader
        return self._default_reader
