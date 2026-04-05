from __future__ import annotations

from pathlib import Path

from indexing.readers.base import BaseReader

# Byte sequences that strongly indicate a binary file
_BINARY_SIGNATURES: list[bytes] = [
    b"\x89PNG",  # PNG
    b"\xff\xd8\xff",  # JPEG
    b"GIF8",  # GIF
    b"BM",  # BMP
    b"\x00\x00\x01\x00",  # ICO
    b"PK\x03\x04",  # ZIP / DOCX / XLSX / PPTX (already handled by dedicated readers)
    b"\xd0\xcf\x11\xe0",  # OLE2 / legacy Office
    b"%PDF",  # PDF (already handled)
    b"\x7fELF",  # ELF binary
    b"MZ",  # Windows PE executable
    b"\x1f\x8b",  # gzip
    b"BZh",  # bzip2
    b"\xfd7zXZ",  # xz
    b"Rar!",  # RAR
    b"\x00\x00\x00\x0cftyp",  # MP4 / MOV
    b"RIFF",  # AVI / WAV
    b"\x49\x49\x2a\x00",  # TIFF LE
    b"\x4d\x4d\x00\x2a",  # TIFF BE
    b"WEBP",  # WebP
]

_PEEK_BYTES = 512


def _is_binary(path: Path) -> bool:
    """Return True if the file looks like binary content."""
    try:
        chunk = path.read_bytes()[:_PEEK_BYTES]
    except OSError:
        return True

    # Check known magic bytes
    for sig in _BINARY_SIGNATURES:
        if chunk.startswith(sig):
            return True

    # Heuristic: if more than 30% of bytes are non-printable (excluding common whitespace), treat as binary
    non_printable = sum(
        1 for b in chunk if b < 0x09 or (0x0E <= b <= 0x1F) or b == 0x7F
    )
    return len(chunk) > 0 and non_printable / len(chunk) > 0.30


class DefaultReader(BaseReader):
    def read_content(self, path: Path) -> str:
        if _is_binary(path):
            return ""
        return path.read_text(encoding="utf-8", errors="ignore")

    def read_sematic(self, path: Path) -> str:
        return self.read_content(path)
