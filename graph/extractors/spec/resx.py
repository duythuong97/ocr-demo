"""ResxExtractor — parse .NET .resx resource files.

Extracts:
  - Every <data name="..."><value>...</value></data> entry
  - Language from filename: Resources.ja.resx → lang=ja, Resources.resx → lang=default
  - Creates a Document node per file (bulk text for RAG), plus individual key→value
    metadata stored as properties for accurate search

Node types: Document (one per .resx file, carrying all resource key/value text)
Relationships: BELONGS_TO (Document → File)
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from graph.extractors.base import BaseExtractor
from graph.db.entities import ExtractionContext, ExtractionResult, GraphEdge, GraphNode
from graph.db import schema as S

logger = logging.getLogger(__name__)

# <data name="SomeKey" xml:space="preserve"> <value>Some text</value> </data>
_DATA_ENTRY = re.compile(
    r'<data\s+name=["\']([^"\']+)["\'][^>]*>\s*<value>([\s\S]*?)</value>',
    re.IGNORECASE,
)

# Extract language code from filename: Resources.ja.resx → "ja"
# Resources.resx → "default"
_LANG_FROM_NAME = re.compile(r'\.([a-z]{2}(?:-[A-Z]{2})?)\.resx$', re.IGNORECASE)


class ResxExtractor(BaseExtractor):
    def can_handle(self, file_path: str, text: str) -> bool:
        return Path(file_path).suffix.lower() == ".resx"

    def extract(self, file_path: str, text: str, context: ExtractionContext) -> ExtractionResult:
        result = ExtractionResult(source_file=file_path, extractor_name="ResxExtractor")
        repository = context.repository
        p = Path(file_path)

        # Detect language from filename suffix
        m_lang = _LANG_FROM_NAME.search(p.name)
        language = m_lang.group(1).lower() if m_lang else "default"

        # Base resource name (strip language suffix)
        base_name = p.name
        if m_lang:
            base_name = base_name[: m_lang.start()] + ".resx"

        # Collect all key-value pairs
        entries: list[tuple[str, str]] = []
        for m in _DATA_ENTRY.finditer(text):
            key = m.group(1).strip()
            value = m.group(2).strip()
            if key and value:
                entries.append((key, value))

        if not entries:
            return result

        # Build combined text for RAG indexing
        combined_text = "\n".join(f"{k}: {v}" for k, v in entries)

        doc_qname = f"Document:{repository}:resx:{p.stem}:{language}"
        result.nodes.append(GraphNode(
            label=S.LABEL_DOCUMENT,
            key="qualified_name",
            key_value=doc_qname,
            properties={
                "name": p.stem,
                "base_resource": base_name,
                "language": language,
                "repository": repository,
                "source_file": file_path,
                "kind": "resx",
                "entry_count": len(entries),
                "text_preview": combined_text[:500],
            },
        ))

        # File node (for BELONGS_TO edge)
        file_qname = f"File:{repository}:{p.as_posix()}"
        result.nodes.append(GraphNode(
            label=S.LABEL_FILE,
            key="qualified_name",
            key_value=file_qname,
            properties={
                "name": p.name,
                "file_path": str(file_path),
                "repository": repository,
            },
        ))
        result.edges.append(GraphEdge(
            from_label=S.LABEL_DOCUMENT,
            from_key="qualified_name",
            from_key_value=doc_qname,
            to_label=S.LABEL_FILE,
            to_key="qualified_name",
            to_key_value=file_qname,
            rel_type=S.REL_BELONGS_TO,
            properties={"language": language},
        ))

        return result
