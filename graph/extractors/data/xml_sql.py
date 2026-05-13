"""XML SQL extractor.

Handles XML files that embed raw SQL:
  - MyBatis / iBATIS mapper XML (select|insert|update|delete elements)
  - Custom SQL XML (any element whose text content looks like SQL)
  - NHibernate HBM files

Creates: Table, Function nodes + WRITES_TO / READS_FROM edges.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from xml.etree import ElementTree

from graph.extractors.base import BaseExtractor
from graph.extractors.code.csharp import (
    _SQL_DELETE, _SQL_INSERT, _SQL_JOIN, _SQL_SELECT, _SQL_UPDATE,
    _looks_like_sql, _strip_table_brackets, _add_unique_node,
)
from graph.db.entities import ExtractionContext, ExtractionResult, GraphEdge, GraphNode
from graph.db import schema as S

logger = logging.getLogger(__name__)

_XML_EXTENSIONS = {".xml"}

# MyBatis statement elements
_MYBATIS_WRITE_TAGS = {"insert", "update", "delete"}
_MYBATIS_READ_TAGS = {"select"}
_MYBATIS_ALL_TAGS = _MYBATIS_WRITE_TAGS | _MYBATIS_READ_TAGS


class XmlSqlExtractor(BaseExtractor):
    def can_handle(self, file_path: str, text: str) -> bool:
        return Path(file_path).suffix.lower() in _XML_EXTENSIONS

    def extract(self, file_path: str, text: str, context: ExtractionContext) -> ExtractionResult:
        result = ExtractionResult(source_file=file_path, extractor_name="XmlSqlExtractor")
        repository = context.repository
        service_name = context.service_name or context.infer_service_from_path(file_path)

        # Try structured XML parse (MyBatis style)
        extracted = _try_mybatis(file_path, text, context, service_name)
        if extracted:
            result.nodes.extend(extracted.nodes)
            result.edges.extend(extracted.edges)
            return result

        # Fallback: text-scan for any SQL-looking content
        _scan_text_sql(text, file_path, context, service_name, result)
        return result


def _try_mybatis(file_path: str, text: str, context: ExtractionContext, service_name: str) -> ExtractionResult | None:
    """Parse MyBatis-style mapper XML."""
    result = ExtractionResult(source_file=file_path, extractor_name="XmlSqlExtractor(mybatis)")
    repository = context.repository
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError:
        return None

    # Check if this is a mapper file
    mapper_namespace = root.get("namespace", "")
    if not mapper_namespace and root.tag not in ("mapper", "sqlMap", "sql-map"):
        return None  # Not a MyBatis mapper, let fallback handle it

    for elem in root.iter():
        tag = elem.tag.lower()
        if tag not in _MYBATIS_ALL_TAGS:
            continue
        stmt_id = elem.get("id", "")
        sql_text = _get_element_text(elem)
        if not sql_text or not _looks_like_sql(sql_text):
            continue

        is_write = tag in _MYBATIS_WRITE_TAGS
        op = tag.upper()

        # Function node = mapper_namespace.statementId
        func_name = f"{mapper_namespace}.{stmt_id}" if mapper_namespace else stmt_id
        func_qname = f"{S.LABEL_FUNCTION}:{repository}:{func_name}"
        _add_unique_node(result, GraphNode(
            label=S.LABEL_FUNCTION,
            key="qualified_name",
            key_value=func_qname,
            properties={
                "qualified_name": func_qname,
                "name": stmt_id,
                "namespace": mapper_namespace,
                "service": service_name,
                "repository": repository,
                "source_file": file_path,
                "mapper_tag": tag,
            },
        ))

        for table_name, detected_op in _extract_tables(sql_text):
            tbl_qname = context.table_qname(table_name)
            _add_unique_node(result, GraphNode(
                label=S.LABEL_TABLE,
                key="qualified_name",
                key_value=tbl_qname,
                properties={
                    "qualified_name": tbl_qname,
                    "name": table_name,
                    "repository": repository,
                    "db_name": context.db_name,
                },
            ))
            rel_type = S.REL_WRITES_TO if detected_op in ("INSERT", "UPDATE", "DELETE") else S.REL_READS_FROM
            result.edges.append(GraphEdge(
                from_label=S.LABEL_FUNCTION, from_key="qualified_name", from_key_value=func_qname,
                to_label=S.LABEL_TABLE, to_key="qualified_name", to_key_value=tbl_qname,
                rel_type=rel_type,
                properties={"operation": detected_op, "source_file": file_path, "sql_snippet": sql_text[:200]},
            ))

    return result if not result.is_empty() else None


def _scan_text_sql(text: str, file_path: str, context: ExtractionContext, service_name: str, result: ExtractionResult) -> None:
    """Generic text scan for SQL-looking blocks in any XML."""
    # Split on XML tags, check each text segment
    repository = context.repository
    segments = re.split(r"<[^>]+>", text)
    for idx, seg in enumerate(segments):
        seg = seg.strip()
        if not seg or not _looks_like_sql(seg):
            continue
        func_name = f"{Path(file_path).stem}_segment_{idx}"
        func_qname = f"{S.LABEL_FUNCTION}:{repository}:{func_name}"
        _add_unique_node(result, GraphNode(
            label=S.LABEL_FUNCTION,
            key="qualified_name",
            key_value=func_qname,
            properties={
                "qualified_name": func_qname,
                "name": func_name,
                "service": service_name,
                "repository": repository,
                "source_file": file_path,
            },
        ))
        for table_name, op in _extract_tables(seg):
            tbl_qname = context.table_qname(table_name)
            _add_unique_node(result, GraphNode(
                label=S.LABEL_TABLE,
                key="qualified_name",
                key_value=tbl_qname,
                properties={
                    "qualified_name": tbl_qname,
                    "name": table_name,
                    "repository": repository,
                    "db_name": context.db_name,
                },
            ))
            rel_type = S.REL_WRITES_TO if op in ("INSERT", "UPDATE", "DELETE") else S.REL_READS_FROM
            result.edges.append(GraphEdge(
                from_label=S.LABEL_FUNCTION, from_key="qualified_name", from_key_value=func_qname,
                to_label=S.LABEL_TABLE, to_key="qualified_name", to_key_value=tbl_qname,
                rel_type=rel_type,
                properties={"operation": op, "source_file": file_path},
            ))


def _get_element_text(elem: ElementTree.Element) -> str:
    """Collect all text content recursively (handles CDATA via itertext)."""
    return " ".join(elem.itertext()).strip()


def _extract_tables(sql: str) -> list[tuple[str, str]]:
    tables = []
    for m in _SQL_INSERT.finditer(sql):
        t = _strip_table_brackets(m.group(1))
        if t: tables.append((t, "INSERT"))
    for m in _SQL_UPDATE.finditer(sql):
        t = _strip_table_brackets(m.group(1))
        if t: tables.append((t, "UPDATE"))
    for m in _SQL_DELETE.finditer(sql):
        t = _strip_table_brackets(m.group(1))
        if t: tables.append((t, "DELETE"))
    for m in _SQL_SELECT.finditer(sql):
        t = _strip_table_brackets(m.group(1))
        if t and t.upper() not in {"SELECT", "WITH", "DUAL", "("}:
            tables.append((t, "SELECT"))
    for m in _SQL_JOIN.finditer(sql):
        t = _strip_table_brackets(m.group(1))
        if t: tables.append((t, "SELECT"))
    return [(t, op) for t, op in tables if len(t) >= 2]
