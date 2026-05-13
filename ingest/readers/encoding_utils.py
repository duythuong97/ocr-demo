"""Encoding detection utility for text-based readers.

Strategy (in order):
1. Strip UTF-8 BOM and try strict UTF-8.
2. Use chardet to detect encoding (confidence ≥ 0.7).
3. Try common Japanese encodings: cp932 (Windows Shift-JIS), euc-jp, iso-2022-jp.
4. Fall back to latin-1 (lossless decode of any byte sequence).
"""
from __future__ import annotations

from pathlib import Path

try:
    import chardet as _chardet
except ImportError:
    _chardet = None  # type: ignore[assignment]

# Japanese encodings tried in order when chardet is unavailable or low-confidence.
_JP_FALLBACKS = ["cp932", "euc_jp", "iso2022_jp", "latin-1"]


def read_text_auto(path: Path) -> str:
    """Read a text file with automatic encoding detection.

    Returns decoded text. Never raises UnicodeDecodeError.
    """
    raw = path.read_bytes()
    return decode_bytes_auto(raw, str(path))


def decode_bytes_auto(raw: bytes, hint: str = "") -> str:
    """Decode *raw* bytes with automatic encoding detection.

    *hint* is used only for debug messages (e.g. the file path).
    """
    if not raw:
        return ""

    # Strip UTF-8 BOM
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8", errors="replace")

    # Strict UTF-8 attempt
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass

    # chardet detection
    if _chardet is not None:
        detected = _chardet.detect(raw[:65536])  # sample first 64 KB for speed
        enc = detected.get("encoding") or ""
        confidence = detected.get("confidence") or 0.0
        if enc and confidence >= 0.70:
            try:
                return raw.decode(enc, errors="replace")
            except (LookupError, UnicodeDecodeError):
                pass

    # Japanese + latin-1 fallback chain
    for enc in _JP_FALLBACKS:
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue

    # Should never reach here (latin-1 never raises)
    return raw.decode("latin-1", errors="replace")
