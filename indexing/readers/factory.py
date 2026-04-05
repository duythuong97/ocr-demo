from __future__ import annotations

from pathlib import Path

from indexing.readers.base import BaseReader
from indexing.readers.default_reader import DefaultReader
from indexing.readers.excel_reader import ExcelReader
from indexing.readers.image_reader import ImageReader
from indexing.readers.pdf_reader import PdfReader
from indexing.readers.powerpoint_reader import PowerPointReader
from indexing.readers.text_reader import TextReader
from indexing.readers.word_reader import WordReader


TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".rst",
    ".json",
    ".xml",
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
    ".html",
}

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".webp"}

WORD_EXTENSIONS = {".docx"}

EXCEL_EXTENSIONS = {".xlsx", ".xlsm", ".xlam", ".xls", ".csv"}

POWERPOINT_EXTENSIONS = {".pptx", ".pptm", ".ppt"}


class ReaderFactory:
    def __init__(self):
        self._text_reader = TextReader()
        self._pdf_reader = PdfReader()
        self._image_reader = ImageReader()
        self._word_reader = WordReader()
        self._excel_reader = ExcelReader()
        self._powerpoint_reader = PowerPointReader()
        self._default_reader = DefaultReader()

    def get_reader(self, path: Path) -> BaseReader:
        ext = path.suffix.lower()
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
