"""XmlReader: extracts human-readable text from XML/MyBatis mapper files.

Strips all XML tags and unwraps CDATA sections so the indexed content
is clean SQL / text rather than raw XML markup.  Falls back to a simple
regex strip if ElementTree cannot parse the document (e.g. malformed XML).
"""
from __future__ import annotations

import re
from pathlib import Path
from xml.etree import ElementTree

from ingest.readers.base import BaseReader
from ingest.readers.encoding_utils import read_text_auto


def _strip_via_re(text: str) -> str:
    """Regex-based fallback: unwrap CDATA, remove PI/comments/tags."""
    # Unwrap CDATA sections — keep the content
    text = re.sub(r"<!\[CDATA\[", "", text)
    text = re.sub(r"\]\]>", "", text)
    # Remove XML processing instructions  <?xml ... ?>
    text = re.sub(r"<\?[^?]*\?>", " ", text)
    # Remove comments <!-- ... -->
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.DOTALL)
    # Remove all remaining tags
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(text.split())


def _strip_via_etree(text: str) -> str:
    """ElementTree-based strip: handles CDATA natively."""
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError:
        return _strip_via_re(text)

    parts: list[str] = []
    # Include meaningful attribute values (id, name, namespace, resultType, …)
    _KEEP_ATTRS = {
        # MyBatis mapper
        "id", "name", "namespace", "resultType", "resultMap",
        "parameterType", "ofType", "javaType", "jdbcType",
        "column", "property", "select", "table",
        # Spring / Hibernate
        "class", "ref", "type", "value",
        "bean", "parent", "factory-bean", "factory-method",
        # General XML
        "key", "code", "label",
    }
    for elem in root.iter():
        for attr, val in elem.attrib.items():
            if attr in _KEEP_ATTRS and val and val.strip():
                parts.append(val.strip())
        if elem.text and elem.text.strip():
            parts.append(elem.text.strip())
        if elem.tail and elem.tail.strip():
            parts.append(elem.tail.strip())
    return " ".join(parts)


class XmlReader(BaseReader):
    """Reader for XML files — returns tag-stripped text for indexing."""

    def read_content(self, path: Path) -> str:
        raw = read_text_auto(path)
        return _strip_via_etree(raw) or _strip_via_re(raw)

    def read_semantic(self, path: Path) -> str:
        return self.read_content(path)
