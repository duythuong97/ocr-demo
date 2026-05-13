"""C# SQL extractor.

Extracts:
  - Table write/read operations from raw SQL strings (plain and @verbatim)
  - Enclosing class + method name (line-number heuristic)
  - Service name from C# namespace declaration
  - Creates: Table, Function, Class, Service nodes
  - Creates: WRITES_TO / READS_FROM / BELONGS_TO edges
"""
from __future__ import annotations

import re
import logging
from pathlib import Path

from graph.extractors.base import BaseExtractor
from graph.db.entities import ExtractionContext, ExtractionResult, GraphEdge, GraphNode
from graph.db import schema as S

logger = logging.getLogger(__name__)

# ── Regex patterns ────────────────────────────────────────────────────────────

# Match C# string literals (plain "..." and verbatim @"...")
_STR_LITERAL = re.compile(
    r'@?"(?:[^"\\]|\\.)*"',
    re.DOTALL,
)

# SQL DML patterns — applied to extracted string content
_SQL_INSERT = re.compile(r'\bINSERT\s+(?:INTO\s+)?(\[?[\w\.]+\]?)', re.IGNORECASE)
_SQL_UPDATE = re.compile(r'\bUPDATE\s+(\[?[\w\.]+\]?)\s+SET', re.IGNORECASE)
_SQL_DELETE = re.compile(r'\bDELETE\s+FROM\s+(\[?[\w\.]+\]?)', re.IGNORECASE)
_SQL_SELECT = re.compile(r'\bFROM\s+(\[?[\w\.]+\]?)(?:\s+(?:AS\s+\w+\s+)?(?:INNER|LEFT|RIGHT|JOIN|WHERE|ORDER|GROUP|\)))?', re.IGNORECASE)
_SQL_JOIN   = re.compile(r'\bJOIN\s+(\[?[\w\.]+\]?)', re.IGNORECASE)

# C# structural patterns — line-by-line
_NS_PATTERN     = re.compile(r'^\s*namespace\s+([\w\.]+)', re.MULTILINE)
_CLASS_PATTERN  = re.compile(r'^\s*(?:public|internal|private|protected|abstract|sealed|static)?\s*(?:partial\s+)?class\s+(\w+)', re.MULTILINE)
_METHOD_PATTERN = re.compile(r'^\s*(?:public|private|protected|internal|override|virtual|async|static)*\s+(?:Task<?[\w<>\[\],\s]*>?|void|[\w<>\[\]]+)\s+(\w+)\s*\(', re.MULTILINE)

_CSHARP_EXTENSIONS = {".cs"}

# ── Stored procedure call patterns ───────────────────────────────────────────
# Dapper: conn.Execute("PKG.PROC", ..., commandType: CommandType.StoredProcedure)
_SP_DAPPER = re.compile(
    r'\.(?:Execute|Query|QueryFirst(?:OrDefault)?|QuerySingle(?:OrDefault)?|ExecuteScalar)\s*(?:<[^>]{0,80}>)?\s*\(\s*@?"([\w\.\$#]{2,80})"[^;]{0,500}?CommandType\.StoredProcedure',
    re.IGNORECASE | re.DOTALL,
)
# ADO.NET: .CommandText = "PKG.PROC" near CommandType.StoredProcedure
_SP_CMD_TEXT = re.compile(
    r'\.CommandText\s*=\s*@?"([\w\.\$#]{2,80})"',
    re.IGNORECASE,
)
_SP_CMD_TYPE_CHECK = re.compile(r'CommandType\.StoredProcedure', re.IGNORECASE)

# EF Core: public DbSet<ClassName> PropertyName { get; set; }
_EF_DBSET = re.compile(
    r'public\s+(?:virtual\s+)?DbSet\s*<\s*(\w+)\s*>\s+(\w+)\s*\{',
    re.MULTILINE,
)

# Skip list for stored proc name validation
_NOT_SP_NAMES = frozenset({
    "SELECT", "INSERT", "UPDATE", "DELETE", "MERGE", "EXEC", "EXECUTE",
    "FROM", "WHERE", "WITH", "AND", "OR", "NOT",
})


def _strip_table_brackets(name: str) -> str:
    return name.strip("[]").split(".")[-1].upper()


class CSharpSqlExtractor(BaseExtractor):
    """Extracts Table read/write nodes from raw SQL in C# source files."""

    def can_handle(self, file_path: str, text: str) -> bool:
        return Path(file_path).suffix.lower() in _CSHARP_EXTENSIONS

    def extract(self, file_path: str, text: str, context: ExtractionContext) -> ExtractionResult:
        result = ExtractionResult(source_file=file_path, extractor_name="CSharpSqlExtractor")
        lines = text.splitlines()

        namespace = _extract_namespace(text)
        service_name = context.infer_service_from_namespace(namespace) if namespace else context.infer_service_from_path(file_path)
        repository = context.repository or Path(file_path).parts[0]

        # Build structural index: line_no → (class_name, method_name)
        class_spans = _build_spans(_CLASS_PATTERN, text)
        method_spans = _build_spans(_METHOD_PATTERN, text)

        # Ensure Service node exists
        svc_qname = f"{S.LABEL_SERVICE}:{repository}:{service_name}"
        result.nodes.append(GraphNode(
            label=S.LABEL_SERVICE,
            key="qualified_name",
            key_value=svc_qname,
            properties={
                "qualified_name": svc_qname,
                "name": service_name,
                "repository": repository,
                "namespace": namespace or "",
                "source_file": file_path,
            },
        ))

        # Scan all string literals for SQL
        for lit_match in _STR_LITERAL.finditer(text):
            literal_text = lit_match.group(0)[1:].strip('"')  # remove leading @ or "
            if not _looks_like_sql(literal_text):
                continue

            start_line = text[:lit_match.start()].count("\n")
            class_name = _resolve_span(class_spans, start_line) or "Unknown"
            method_name = _resolve_span(method_spans, start_line) or "Unknown"

            class_qname = f"{S.LABEL_CLASS}:{repository}:{namespace}.{class_name}" if namespace else f"{S.LABEL_CLASS}:{repository}:{class_name}"
            func_qname = f"{S.LABEL_FUNCTION}:{repository}:{namespace}.{class_name}.{method_name}" if namespace else f"{S.LABEL_FUNCTION}:{repository}:{class_name}.{method_name}"

            # Ensure Class node
            _add_unique_node(result, GraphNode(
                label=S.LABEL_CLASS,
                key="qualified_name",
                key_value=class_qname,
                properties={
                    "qualified_name": class_qname,
                    "name": class_name,
                    "namespace": namespace or "",
                    "repository": repository,
                    "service": service_name,
                    "source_file": file_path,
                },
            ))

            # Ensure Function node
            _add_unique_node(result, GraphNode(
                label=S.LABEL_FUNCTION,
                key="qualified_name",
                key_value=func_qname,
                properties={
                    "qualified_name": func_qname,
                    "name": method_name,
                    "class_name": class_name,
                    "namespace": namespace or "",
                    "repository": repository,
                    "service": service_name,
                    "source_file": file_path,
                    "line": start_line + 1,
                },
            ))

            # Class → Service
            result.edges.append(GraphEdge(
                from_label=S.LABEL_CLASS, from_key="qualified_name", from_key_value=class_qname,
                to_label=S.LABEL_SERVICE, to_key="qualified_name", to_key_value=svc_qname,
                rel_type=S.REL_BELONGS_TO,
            ))
            # Function → Class
            result.edges.append(GraphEdge(
                from_label=S.LABEL_FUNCTION, from_key="qualified_name", from_key_value=func_qname,
                to_label=S.LABEL_CLASS, to_key="qualified_name", to_key_value=class_qname,
                rel_type=S.REL_BELONGS_TO,
            ))

            # Extract table operations
            ops: list[tuple[str, str]] = []  # (table_name, operation)
            for m in _SQL_INSERT.finditer(literal_text):
                ops.append((_strip_table_brackets(m.group(1)), "INSERT"))
            for m in _SQL_UPDATE.finditer(literal_text):
                ops.append((_strip_table_brackets(m.group(1)), "UPDATE"))
            for m in _SQL_DELETE.finditer(literal_text):
                ops.append((_strip_table_brackets(m.group(1)), "DELETE"))
            for m in _SQL_SELECT.finditer(literal_text):
                tbl = _strip_table_brackets(m.group(1))
                if tbl and tbl.upper() not in {"SELECT", "WITH", "DUAL", "("}:
                    ops.append((tbl, "SELECT"))
            for m in _SQL_JOIN.finditer(literal_text):
                tbl = _strip_table_brackets(m.group(1))
                if tbl:
                    ops.append((tbl, "SELECT"))

            for table_name, op in ops:
                if not table_name or len(table_name) < 2:
                    continue
                tbl_qname = context.table_qname(table_name)
                _add_unique_node(result, GraphNode(
                    label=S.LABEL_TABLE,
                    key="qualified_name",
                    key_value=tbl_qname,
                    properties={
                        "qualified_name": tbl_qname,
                        "name": table_name,
                        "repository": repository,
                    },
                ))
                rel_type = S.REL_WRITES_TO if op in ("INSERT", "UPDATE", "DELETE") else S.REL_READS_FROM
                result.edges.append(GraphEdge(
                    from_label=S.LABEL_FUNCTION, from_key="qualified_name", from_key_value=func_qname,
                    to_label=S.LABEL_TABLE, to_key="qualified_name", to_key_value=tbl_qname,
                    rel_type=rel_type,
                    properties={
                        "operation": op,
                        "line": start_line + 1,
                        "source_file": file_path,
                        "sql_snippet": literal_text[:200],
                    },
                ))

        # ── Stored procedure calls (Dapper + ADO.NET) ────────────────────────
        namespace = _extract_namespace(text)

        # Pattern 1: Dapper — proc name in first arg + CommandType.StoredProcedure
        for m in _SP_DAPPER.finditer(text):
            proc_name = m.group(1).strip()
            if not proc_name or proc_name.upper() in _NOT_SP_NAMES:
                continue
            start_line = text[:m.start()].count("\n")
            class_name = _resolve_span(class_spans, start_line) or "Unknown"
            method_name = _resolve_span(method_spans, start_line) or "Unknown"
            func_qname = (
                f"{S.LABEL_FUNCTION}:{repository}:{namespace}.{class_name}.{method_name}"
                if namespace
                else f"{S.LABEL_FUNCTION}:{repository}:{class_name}.{method_name}"
            )
            target_qname = f"{S.LABEL_FUNCTION}:{repository}:{proc_name.upper()}"
            result.edges.append(GraphEdge(
                from_label=S.LABEL_FUNCTION, from_key="qualified_name", from_key_value=func_qname,
                to_label=S.LABEL_FUNCTION,   to_key="qualified_name", to_key_value=target_qname,
                rel_type=S.REL_CALLS,
                properties={"proc_name": proc_name, "call_type": "StoredProcedure",
                            "source_file": file_path, "line": start_line + 1},
            ))

        # Pattern 2: ADO.NET — .CommandText = "PKG.PROC" near CommandType.StoredProcedure
        for m_text in _SP_CMD_TEXT.finditer(text):
            proc_name = m_text.group(1).strip()
            if not proc_name or proc_name.upper() in _NOT_SP_NAMES:
                continue
            win_start = max(0, m_text.start() - 100)
            win_end   = min(len(text), m_text.end() + 600)
            if not _SP_CMD_TYPE_CHECK.search(text[win_start:win_end]):
                continue
            start_line = text[:m_text.start()].count("\n")
            class_name = _resolve_span(class_spans, start_line) or "Unknown"
            method_name = _resolve_span(method_spans, start_line) or "Unknown"
            func_qname = (
                f"{S.LABEL_FUNCTION}:{repository}:{namespace}.{class_name}.{method_name}"
                if namespace
                else f"{S.LABEL_FUNCTION}:{repository}:{class_name}.{method_name}"
            )
            target_qname = f"{S.LABEL_FUNCTION}:{repository}:{proc_name.upper()}"
            result.edges.append(GraphEdge(
                from_label=S.LABEL_FUNCTION, from_key="qualified_name", from_key_value=func_qname,
                to_label=S.LABEL_FUNCTION,   to_key="qualified_name", to_key_value=target_qname,
                rel_type=S.REL_CALLS,
                properties={"proc_name": proc_name, "call_type": "StoredProcedure",
                            "source_file": file_path, "line": start_line + 1},
            ))

        # ── EF Core DbSet<T> → Table node ──────────────────────────────────────
        for m in _EF_DBSET.finditer(text):
            entity_class = m.group(1)
            prop_name = m.group(2).upper()   # e.g. Employees → EMPLOYEES
            tbl_qname = f"{S.LABEL_TABLE}:{repository}:{prop_name}"
            _add_unique_node(result, GraphNode(
                label=S.LABEL_TABLE,
                key="qualified_name",
                key_value=tbl_qname,
                properties={
                    "qualified_name": tbl_qname,
                    "name": prop_name,
                    "repository": repository,
                    "entity_class": entity_class,
                    "source": "ef_dbset",
                },
            ))

        return result


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_namespace(text: str) -> str:
    m = _NS_PATTERN.search(text)
    return m.group(1) if m else ""


def _looks_like_sql(s: str) -> bool:
    upper = s.upper()
    return any(kw in upper for kw in ("SELECT", "INSERT INTO", "UPDATE", "DELETE FROM", "EXEC ", "EXECUTE "))


def _build_spans(pattern: re.Pattern, text: str) -> list[tuple[int, str]]:
    """Return list of (line_no, name) sorted by line_no."""
    spans = []
    for m in pattern.finditer(text):
        line_no = text[:m.start()].count("\n")
        spans.append((line_no, m.group(1)))
    return sorted(spans, key=lambda x: x[0])


def _resolve_span(spans: list[tuple[int, str]], line_no: int) -> str | None:
    """Return the name whose declaration appears closest above line_no."""
    result = None
    for span_line, name in spans:
        if span_line <= line_no:
            result = name
        else:
            break
    return result


def _add_unique_node(result: ExtractionResult, node: GraphNode) -> None:
    existing = {n.key_value for n in result.nodes}
    if node.key_value not in existing:
        result.nodes.append(node)
