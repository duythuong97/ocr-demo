from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

from ingest.readers.base import BaseReader
from ingest.readers.encoding_utils import read_text_auto

# Tags whose *content* we skip entirely (not just the tag itself)
_SKIP_CONTENT_TAGS = {"script", "style", "noscript", "head"}

# For Vue SFC / Razor: skip code blocks but keep template text
_SKIP_CONTENT_TAGS_VUE = {"script", "style"}


class _TextExtractor(HTMLParser):
    """HTMLParser subclass that collects visible text."""

    # Attributes whose values contain human-readable text
    _TEXT_ATTRS = {"alt", "title", "aria-label", "placeholder"}

    def __init__(self, skip_tags: set[str] = _SKIP_CONTENT_TAGS) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_tags = skip_tags
        self._skip_depth: int = 0
        self._current_skip_tag: str | None = None
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in self._skip_tags:
            if self._skip_depth == 0:
                self._current_skip_tag = tag
            self._skip_depth += 1
            return
        if self._skip_depth > 0:
            return
        # Emit alt/title/aria-label text from void elements like <img>, <input>
        for name, value in attrs:
            if name in self._TEXT_ATTRS and value and value.strip():
                self._parts.append(value.strip())

    def handle_endtag(self, tag: str) -> None:
        if tag in self._skip_tags and self._skip_depth > 0:
            self._skip_depth -= 1
            if self._skip_depth == 0:
                self._current_skip_tag = None

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return
        text = data.strip()
        if text:
            self._parts.append(text)

    def get_text(self) -> str:
        return " ".join(self._parts)


def _strip_razor_syntax(text: str) -> str:
    """Remove Razor/CSHTML server-side code markers (@...) for cleaner text."""
    # Remove @{ ... } blocks
    text = re.sub(r"@\{[^}]*\}", " ", text, flags=re.DOTALL)
    # Remove @(...) expressions
    text = re.sub(r"@\([^)]*\)", " ", text)
    # Remove remaining @identifier tokens
    text = re.sub(r"@[\w.]+", " ", text)
    return text


class HtmlReader(BaseReader):
    """Reader for HTML-like markup files.

    Strips tags and skips <script>/<style> blocks so only human-readable
    text is returned for indexing.

    Supported file types (configured in factory.py):
      - .html, .htm   — standard HTML
      - .vue          — Vue SFC (skips <script> and <style>, keeps <template> text)
      - .cshtml, .razor — ASP.NET Razor (strips tags + Razor syntax)
      - .svg          — SVG XML (skips <script>, extracts <text> / <title> content)
    """

    def _extract(self, path: Path) -> str:
        raw = read_text_auto(path)

        ext = path.suffix.lower()
        skip_tags = _SKIP_CONTENT_TAGS_VUE if ext == ".vue" else _SKIP_CONTENT_TAGS

        parser = _TextExtractor(skip_tags=skip_tags)
        try:
            parser.feed(raw)
            text = parser.get_text()
        except Exception:
            # Fallback: naive tag-strip with regex
            text = re.sub(r"<[^>]+>", " ", raw)

        if ext in (".cshtml", ".razor"):
            text = _strip_razor_syntax(text)

        # Collapse excessive whitespace
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def read_content(self, path: Path) -> str:
        return self._extract(path)

    def read_semantic(self, path: Path) -> str:
        return self._extract(path)
