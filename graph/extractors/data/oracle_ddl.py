"""Oracle DDL extractor.

Pre-seeds Table nodes from Oracle SQL DDL files.

Handles:
  - CREATE [OR REPLACE] [GLOBAL TEMPORARY] TABLE [schema.]name (col_defs)
  - ALTER TABLE [schema.]name ADD (col_def)
  - CREATE [OR REPLACE] [FORCE] VIEW [schema.]name AS SELECT ...

Creates:
  - Table nodes with full column metadata (name, type, nullable, pk_columns)
  - View nodes stored as Table nodes with is_view=True
"""
from __future__ import annotations

import json
import re
import logging
from pathlib import Path

from graph.extractors.base import BaseExtractor
from graph.db.entities import ExtractionContext, ExtractionResult, GraphNode
from graph.db import schema as S

logger = logging.getLogger(__name__)

_DDL_EXTENSIONS = {".sql", ".ddl"}

# Quick-check: must contain CREATE TABLE or CREATE VIEW
_HAS_DDL = re.compile(r"\bCREATE\b.{0,30}?\b(TABLE|VIEW)\b", re.IGNORECASE | re.DOTALL)

# CREATE [OR REPLACE] [GLOBAL TEMPORARY] TABLE [schema.]name (
# group 1 = schema (optional, may be None), group 2 = table name
_CREATE_TABLE_RE = re.compile(
    r"\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:GLOBAL\s+TEMPORARY\s+)?TABLE\s+"
    r'(?:("?[\w$#]+"?)\s*\.\s*)?"?([\w$#]+)"?\s*\(',
    re.IGNORECASE,
)

# CREATE [OR REPLACE] [FORCE] VIEW [schema.]name AS
# group 1 = schema (optional, may be None), group 2 = view name
_CREATE_VIEW_RE = re.compile(
    r"\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:FORCE\s+)?VIEW\s+"
    r'(?:("?[\w$#]+"?)\s*\.\s*)?"?([\w$#]+)"?\s+AS\b',
    re.IGNORECASE,
)

# ALTER TABLE [schema.]name ADD (...)
# group 1 = schema (optional, may be None), group 2 = table name
_ALTER_TABLE_ADD_RE = re.compile(
    r"\bALTER\s+TABLE\s+"
    r'(?:("?[\w$#]+"?)\s*\.\s*)?"?([\w$#]+)"?\s+ADD\b',
    re.IGNORECASE,
)

# Single column definition: name  TYPE[(precision[,scale])] [BYTE|CHAR] [NOT NULL] [DEFAULT ...]
_COLUMN_DEF_RE = re.compile(
    r'^"?([\w$#]+)"?\s+([\w$#]+(?:\s*\([\d\s,]+\))?(?:\s+(?:BYTE|CHAR))?)'
    r'(?:\s+(NOT\s+NULL|NULL))?',
    re.IGNORECASE,
)

# Keywords that start a constraint — skip these lines from column parsing
_CONSTRAINT_STARTS = frozenset({
    "CONSTRAINT", "PRIMARY", "FOREIGN", "UNIQUE", "CHECK",
    "SUPPLEMENTAL", "PARTITION", "TABLESPACE", "STORAGE",
    "ENABLE", "DISABLE", "LOB", "XMLTYPE", "NESTED", "INDEX",
    "SCOPE", "REF", "PERIOD",
})

# Oracle system objects to skip
_SYSTEM_OBJECT_RE = re.compile(
    r"^(?:DUAL|SYS_|ALL_|DBA_|USER_|V\$|GV\$|XMLTABLE|MVIEW|SYS\.)",
    re.IGNORECASE,
)


class OracleDdlExtractor(BaseExtractor):
    def can_handle(self, file_path: str, text: str) -> bool:
        if Path(file_path).suffix.lower() not in _DDL_EXTENSIONS:
            return False
        return bool(_HAS_DDL.search(text[:10_000]))

    def extract(self, file_path: str, text: str, context: ExtractionContext) -> ExtractionResult:
        result = ExtractionResult(source_file=file_path, extractor_name="OracleDdlExtractor")
        repository = context.repository

        # ── CREATE TABLE ─────────────────────────────────────────────────────
        ctx_schema = context.extra_tags.get("schema", "") or ""
        schema_db_map: dict[str, str] = context.extra_tags.get("schema_db_map", {}) or {}

        def _qname_for(schema_val: str, full_name_val: str) -> str:
            """Build Table qname, overriding db_name via schema_db_map when available."""
            eff_db = schema_db_map.get(schema_val, context.db_name) if schema_val else context.db_name
            return f"Table:{eff_db}:{full_name_val}" if eff_db else f"Table:{full_name_val}"

        def _eff_db(schema_val: str) -> str | None:
            return schema_db_map.get(schema_val, context.db_name) if schema_val else context.db_name
        for m in _CREATE_TABLE_RE.finditer(text):
            schema_raw = (m.group(1) or "").strip('"').upper()
            schema = schema_raw or ctx_schema
            table_name = m.group(2).upper()
            full_name = f"{schema}.{table_name}" if schema else table_name
            if _is_system(table_name):
                continue

            # m.end() - 1 because the regex consumed the opening '('
            col_block = _extract_paren_block(text, m.end() - 1)
            columns = _parse_columns(col_block) if col_block else []
            pk_cols = [c["name"] for c in columns if c.get("pk")]

            qname = _qname_for(schema, full_name)
            _upsert_node(result, GraphNode(
                label=S.LABEL_TABLE,
                key="qualified_name",
                key_value=qname,
                properties={
                    "qualified_name": qname,
                    "name": table_name,
                    "schema": schema or None,
                    "repository": repository,
                    "db_name": _eff_db(schema),
                    "source_file": file_path,
                    "columns": [c["name"] for c in columns],
                    "column_types": json.dumps({c["name"]: c["type"] for c in columns}),
                    "pk_columns": pk_cols,
                    "column_count": len(columns),
                    "layer": "data",
                    "source": "ddl",
                },
            ))

        # ── CREATE VIEW ───────────────────────────────────────────────────────
        for m in _CREATE_VIEW_RE.finditer(text):
            schema_raw = (m.group(1) or "").strip('"').upper()
            schema = schema_raw or ctx_schema
            view_name = m.group(2).upper()
            full_name = f"{schema}.{view_name}" if schema else view_name
            if _is_system(view_name):
                continue

            qname = _qname_for(schema, full_name)
            _upsert_node(result, GraphNode(
                label=S.LABEL_TABLE,
                key="qualified_name",
                key_value=qname,
                properties={
                    "qualified_name": qname,
                    "name": view_name,
                    "schema": schema or None,
                    "repository": repository,
                    "db_name": _eff_db(schema),
                    "source_file": file_path,
                    "is_view": True,
                    "layer": "data",
                    "source": "ddl",
                },
            ))

        # ── ALTER TABLE ... ADD ───────────────────────────────────────────────
        for m in _ALTER_TABLE_ADD_RE.finditer(text):
            schema_raw = (m.group(1) or "").strip('"').upper()
            schema = schema_raw or ctx_schema
            table_name = m.group(2).upper()
            full_name = f"{schema}.{table_name}" if schema else table_name
            if _is_system(table_name):
                continue
            qname = _qname_for(schema, full_name)
            # Only create a stub node — do not overwrite a richer DDL-seeded node
            _upsert_node(result, GraphNode(
                label=S.LABEL_TABLE,
                key="qualified_name",
                key_value=qname,
                properties={
                    "qualified_name": qname,
                    "name": table_name,
                    "schema": schema or None,
                    "repository": repository,
                    "db_name": _eff_db(schema),
                    "source_file": file_path,
                    "layer": "data",
                    "source": "ddl_alter",
                },
            ))

        return result


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_paren_block(text: str, start: int) -> str:
    """Return text between the '(' at position `start` and its matching ')'."""
    depth = 0
    block_start = -1
    for i in range(start, len(text)):
        ch = text[i]
        if ch == "(":
            depth += 1
            if depth == 1:
                block_start = i + 1
        elif ch == ")":
            depth -= 1
            if depth == 0 and block_start != -1:
                return text[block_start:i]
    return ""


def _split_at_depth0_comma(block: str) -> list[str]:
    """Split `block` at commas that are not inside parentheses."""
    segments: list[str] = []
    depth = 0
    buf: list[str] = []
    for ch in block:
        if ch == "(":
            depth += 1
            buf.append(ch)
        elif ch == ")":
            depth -= 1
            buf.append(ch)
        elif ch == "," and depth == 0:
            segments.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    tail = "".join(buf).strip()
    if tail:
        segments.append(tail)
    return segments


def _parse_columns(block: str) -> list[dict]:
    """Parse column definitions from a CREATE TABLE column-list block."""
    # Find inline PRIMARY KEY constraint for the whole table
    pk_cols: set[str] = set()
    pk_table_match = re.search(
        r"(?:CONSTRAINT\s+\w+\s+)?PRIMARY\s+KEY\s*\(([^)]+)\)",
        block, re.IGNORECASE,
    )
    if pk_table_match:
        pk_cols = {c.strip().strip('"').upper() for c in pk_table_match.group(1).split(",")}

    columns: list[dict] = []
    for seg in _split_at_depth0_comma(block):
        col = _parse_one_column(seg.strip(), pk_cols)
        if col:
            columns.append(col)
    return columns


def _parse_one_column(seg: str, pk_cols: set[str]) -> dict | None:
    if not seg:
        return None
    # Remove single-line SQL comments
    seg = re.sub(r"--[^\n]*", "", seg).strip()
    if not seg:
        return None
    first_word = seg.split()[0].upper().strip('"')
    if first_word in _CONSTRAINT_STARTS:
        return None
    m = _COLUMN_DEF_RE.match(seg)
    if not m:
        return None
    col_name = m.group(1).upper()
    col_type = m.group(2).upper().strip()
    nullable_kw = (m.group(3) or "").upper()
    is_pk = col_name in pk_cols or bool(re.search(r"\bPRIMARY\s+KEY\b", seg, re.IGNORECASE))
    return {
        "name": col_name,
        "type": col_type,
        "nullable": nullable_kw != "NOT NULL",
        "pk": is_pk,
    }


def _is_system(name: str) -> bool:
    return bool(_SYSTEM_OBJECT_RE.match(name))


def _upsert_node(result: ExtractionResult, node: GraphNode) -> None:
    existing = {n.key_value for n in result.nodes}
    if node.key_value not in existing:
        result.nodes.append(node)
