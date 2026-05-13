"""Oracle PL/SQL extractor.

Parses Oracle PL/SQL source files: packages, standalone procedures/functions,
triggers. Extracts SQL DML statements and links them to the enclosing
procedure or function via WRITES_TO / READS_FROM edges.

File extensions handled:
  .pks  — package specification
  .pkb  — package body
  .pck  — package (combined spec + body)
  .pls  — PL/SQL source
  .plb  — PL/SQL library (wrapped/unwrapped)
  .fnc  — standalone function
  .prc  — standalone procedure
  .trg  — trigger
  .sql  — if content contains CREATE OR REPLACE PACKAGE / PROCEDURE / FUNCTION / TRIGGER

Creates:
  - Class node  per package  (LABEL_CLASS,   object_type="PACKAGE")
  - Function node per proc/func inside package  (LABEL_FUNCTION)
  - Function node for trigger body              (LABEL_FUNCTION, proc_type="TRIGGER")
  - Table nodes referenced by SQL              (LABEL_TABLE)
  - Edges: BELONGS_TO (Function→Class), WRITES_TO / READS_FROM (Function→Table)
  - Edge:  TRIGGERS (TriggerFn→Table  — the table that fires the trigger)
"""
from __future__ import annotations

import re
import logging
from pathlib import Path

from graph.extractors.base import BaseExtractor
from graph.db.entities import ExtractionContext, ExtractionResult, GraphEdge, GraphNode
from graph.db import schema as S

logger = logging.getLogger(__name__)

# ── File type detection ───────────────────────────────────────────────────────
_PLSQL_EXTENSIONS = {".pks", ".pkb", ".pck", ".pls", ".plb", ".fnc", ".prc", ".trg"}
_SQL_EXTENSION = ".sql"

_HAS_PLSQL = re.compile(
    r"\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:EDITIONABLE\s+)?"
    r"(?:PACKAGE|PROCEDURE|FUNCTION|TRIGGER)\b",
    re.IGNORECASE,
)

# ── Structural patterns ───────────────────────────────────────────────────────

# CREATE [OR REPLACE] PACKAGE [BODY] [schema.]name  AS|IS
_PKG_RE = re.compile(
    r"\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:EDITIONABLE\s+)?PACKAGE\s+(?:BODY\s+)?"
    r'(?:"?[\w$#]+"?\s*\.\s*)?"?([\w$#]+)"?\s*(?:AS|IS)\b',
    re.IGNORECASE,
)

# PROCEDURE name  (may be in spec or body — we want both for the span map)
_PROC_RE = re.compile(
    r"\bPROCEDURE\s+\"?([\w$#]+)\"?",
    re.IGNORECASE,
)

# FUNCTION name  RETURN ...
_FUNC_RE = re.compile(
    r"\bFUNCTION\s+\"?([\w$#]+)\"?\s*(?:\([^)]{0,300}\))?\s*RETURN\b",
    re.IGNORECASE | re.DOTALL,
)

# CREATE [OR REPLACE] TRIGGER name  BEFORE|AFTER|INSTEAD  dml_event  ON [schema.]table
_TRIGGER_RE = re.compile(
    r"\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:EDITIONABLE\s+)?TRIGGER\s+"
    r'(?:"?[\w$#]+"?\s*\.\s*)?"?([\w$#]+)"?\s*\n?'
    r"\s*(?:BEFORE|AFTER|INSTEAD\s+OF)\s+"
    r"(?:INSERT|UPDATE|DELETE|INSERT\s+OR\s+UPDATE|INSERT\s+OR\s+DELETE"
    r"|UPDATE\s+OR\s+DELETE|INSERT\s+OR\s+UPDATE\s+OR\s+DELETE)"
    r"(?:\s+OF\s+[\w$#,\s]+)?\s+ON\s+"
    r'(?:"?[\w$#]+"?\s*\.\s*)?"?([\w$#]+)"?',
    re.IGNORECASE | re.DOTALL,
)

# ── SQL DML patterns (Oracle-specific) ────────────────────────────────────────

# INSERT [ALL] INTO [schema.]table
_SQL_INSERT = re.compile(
    r"\bINSERT\s+(?:ALL\s+)?INTO\s+"
    r'(?:"?[\w$#]+"?\s*\.\s*)?"?([\w$#]+)"?',
    re.IGNORECASE,
)

# UPDATE [schema.]table  SET
_SQL_UPDATE = re.compile(
    r"\bUPDATE\s+(?:"
    r'(?:"?[\w$#]+"?\s*\.\s*)?"?([\w$#]+)"?'
    r")\s+SET\b",
    re.IGNORECASE,
)

# DELETE FROM [schema.]table
_SQL_DELETE = re.compile(
    r"\bDELETE\s+FROM\s+"
    r'(?:"?[\w$#]+"?\s*\.\s*)?"?([\w$#]+)"?',
    re.IGNORECASE,
)

# MERGE INTO [schema.]table
_SQL_MERGE = re.compile(
    r"\bMERGE\s+INTO\s+"
    r'(?:"?[\w$#]+"?\s*\.\s*)?"?([\w$#]+)"?',
    re.IGNORECASE,
)

# FROM [schema.]table  — excludes table-function calls e.g. TABLE(...)
_SQL_FROM = re.compile(
    r"\bFROM\s+"
    r'(?:"?[\w$#]+"?\s*\.\s*)?"?([\w$#]+)"?'
    r"(?!\s*\()",
    re.IGNORECASE,
)

# [LEFT|RIGHT|INNER|OUTER|CROSS|FULL] JOIN [schema.]table
_SQL_JOIN = re.compile(
    r"\bJOIN\s+"
    r'(?:"?[\w$#]+"?\s*\.\s*)?"?([\w$#]+)"?',
    re.IGNORECASE,
)

# EXECUTE IMMEDIATE 'literal sql'  (not EXECUTE IMMEDIATE variable)
_EXEC_IMMEDIATE = re.compile(
    r"\bEXECUTE\s+IMMEDIATE\s+'([^']+)'",
    re.IGNORECASE,
)

# Inter-package call at statement level: PKG_NAME.PROC_NAME(
# Anchored to statement start (after ; or BEGIN/THEN/ELSE/LOOP/newline+spaces)
_PKG_CALL_RE = re.compile(
    r'(?:^|;|\bBEGIN\b|\bTHEN\b|\bELSE\b|\bLOOP\b|\bRETURN\b)\s+'
    r'([A-Z][A-Z0-9_$#]{2,29})\.([A-Z][A-Z0-9_$#]{1,29})\s*\(',
    re.IGNORECASE | re.MULTILINE,
)

# Oracle built-in packages to exclude from inter-package call detection
_ORACLE_BUILTIN_PKGS: frozenset[str] = frozenset({
    "DBMS_OUTPUT", "DBMS_SQL", "DBMS_LOB", "DBMS_UTILITY", "DBMS_METADATA",
    "DBMS_LOCK", "DBMS_ALERT", "DBMS_PIPE", "DBMS_SCHEDULER", "DBMS_JOB",
    "DBMS_CRYPTO", "DBMS_RANDOM", "DBMS_TRANSACTION", "DBMS_XMLGEN",
    "UTL_FILE", "UTL_HTTP", "UTL_SMTP", "UTL_RAW", "UTL_I18N", "UTL_URL",
    "APEX_APPLICATION", "APEX_UTIL", "APEX_JSON", "APEX_ITEM",
    "SYS", "STANDARD",
})

# PL/SQL keywords that can precede a dot and look like package names
_PLSQL_KW_PREFIXES: frozenset[str] = frozenset({
    "IF", "END", "ELSIF", "EXCEPTION", "WHEN", "INTO", "FROM",
    "HAVING", "GROUP", "ORDER", "WHERE", "ON", "SET", "IN", "OUT",
})

# Oracle objects to skip (pseudo-tables, system catalog, built-in functions)
_SKIP_NAMES: frozenset[str] = frozenset({
    "DUAL", "ROWNUM", "ROWID", "SYSDATE", "SYSTIMESTAMP", "LEVEL",
    "XMLTABLE", "TABLE", "VIEW", "INDEX", "SELECT", "WITH",
})
_SYS_PREFIX_RE = re.compile(
    r"^(?:SYS_|ALL_|DBA_|USER_|V\$|GV\$|DBMS_|UTL_|APEX_|MVIEW)",
    re.IGNORECASE,
)


class OraclePlSqlExtractor(BaseExtractor):
    def can_handle(self, file_path: str, text: str) -> bool:
        ext = Path(file_path).suffix.lower()
        if ext in _PLSQL_EXTENSIONS:
            return True
        if ext == _SQL_EXTENSION:
            return bool(_HAS_PLSQL.search(text[:3_000]))
        return False

    def extract(self, file_path: str, text: str, context: ExtractionContext) -> ExtractionResult:
        result = ExtractionResult(source_file=file_path, extractor_name="OraclePlSqlExtractor")
        repository = context.repository
        service = context.service_name or context.infer_service_from_path(file_path)

        # ── Package node ──────────────────────────────────────────────────────
        pkg_name: str | None = None
        pkg_qname: str | None = None
        pm = _PKG_RE.search(text)
        if pm:
            pkg_name = pm.group(1).upper()
            pkg_qname = f"{S.LABEL_CLASS}:{repository}:{pkg_name}"
            _add_unique(result, GraphNode(
                label=S.LABEL_CLASS,
                key="qualified_name",
                key_value=pkg_qname,
                properties={
                    "qualified_name": pkg_qname,
                    "name": pkg_name,
                    "service": service,
                    "repository": repository,
                    "source_file": file_path,
                    "object_type": "PACKAGE",
                    "layer": "logic",
                },
            ))

        # ── Procedure / Function spans ────────────────────────────────────────
        # spans: sorted list of (line_no, func_qname) used to assign SQL ops
        spans: list[tuple[int, str]] = []

        for m in _PROC_RE.finditer(text):
            fn = m.group(1).upper()
            full = f"{pkg_name}.{fn}" if pkg_name else fn
            qname = f"{S.LABEL_FUNCTION}:{repository}:{full}"
            line = _line_of(text, m.start())
            spans.append((line, qname))
            _add_unique(result, GraphNode(
                label=S.LABEL_FUNCTION,
                key="qualified_name",
                key_value=qname,
                properties={
                    "qualified_name": qname,
                    "name": fn,
                    "package": pkg_name or "",
                    "service": service,
                    "repository": repository,
                    "source_file": file_path,
                    "proc_type": "PROCEDURE",
                    "layer": "logic",
                },
            ))
            if pkg_qname:
                result.edges.append(GraphEdge(
                    from_label=S.LABEL_FUNCTION, from_key="qualified_name", from_key_value=qname,
                    to_label=S.LABEL_CLASS,    to_key="qualified_name", to_key_value=pkg_qname,
                    rel_type=S.REL_BELONGS_TO,
                ))

        for m in _FUNC_RE.finditer(text):
            fn = m.group(1).upper()
            full = f"{pkg_name}.{fn}" if pkg_name else fn
            qname = f"{S.LABEL_FUNCTION}:{repository}:{full}"
            line = _line_of(text, m.start())
            spans.append((line, qname))
            _add_unique(result, GraphNode(
                label=S.LABEL_FUNCTION,
                key="qualified_name",
                key_value=qname,
                properties={
                    "qualified_name": qname,
                    "name": fn,
                    "package": pkg_name or "",
                    "service": service,
                    "repository": repository,
                    "source_file": file_path,
                    "proc_type": "FUNCTION",
                    "layer": "logic",
                },
            ))
            if pkg_qname:
                result.edges.append(GraphEdge(
                    from_label=S.LABEL_FUNCTION, from_key="qualified_name", from_key_value=qname,
                    to_label=S.LABEL_CLASS,    to_key="qualified_name", to_key_value=pkg_qname,
                    rel_type=S.REL_BELONGS_TO,
                ))

        spans.sort(key=lambda x: x[0])

        # Fallback when no procedures found (e.g. anonymous block or .fnc/.prc with single body)
        fallback_qname: str | None = None
        if not spans:
            stem = Path(file_path).stem.upper()
            fallback_qname = f"{S.LABEL_FUNCTION}:{repository}:{stem}"
            _add_unique(result, GraphNode(
                label=S.LABEL_FUNCTION,
                key="qualified_name",
                key_value=fallback_qname,
                properties={
                    "qualified_name": fallback_qname,
                    "name": stem,
                    "service": service,
                    "repository": repository,
                    "source_file": file_path,
                    "layer": "logic",
                },
            ))

        ctx_schema = (context.extra_tags.get("schema", "") or "").upper()

        # ── SQL DML scan ─────────────────────────────────────────────────────
        # Collect (line_no, table_name, operation) triples from the entire file
        ops: list[tuple[int, str, str]] = []

        for m in _SQL_INSERT.finditer(text):
            t = _norm(m.group(1))
            if not _skip(t):
                ops.append((_line_of(text, m.start()), t, "INSERT"))

        for m in _SQL_UPDATE.finditer(text):
            t = _norm(m.group(1))
            if not _skip(t):
                ops.append((_line_of(text, m.start()), t, "UPDATE"))

        for m in _SQL_DELETE.finditer(text):
            t = _norm(m.group(1))
            if not _skip(t):
                ops.append((_line_of(text, m.start()), t, "DELETE"))

        for m in _SQL_MERGE.finditer(text):
            t = _norm(m.group(1))
            if not _skip(t):
                ops.append((_line_of(text, m.start()), t, "MERGE"))

        for m in _SQL_FROM.finditer(text):
            t = _norm(m.group(1))
            if not _skip(t):
                ops.append((_line_of(text, m.start()), t, "SELECT"))

        for m in _SQL_JOIN.finditer(text):
            t = _norm(m.group(1))
            if not _skip(t):
                ops.append((_line_of(text, m.start()), t, "SELECT"))

        # EXECUTE IMMEDIATE with string literal — parse the embedded SQL
        for m in _EXEC_IMMEDIATE.finditer(text):
            for t, op in _tables_from_sql_literal(m.group(1)):
                ops.append((_line_of(text, m.start()), t, op))

        # ── Assign each op to enclosing procedure/function ────────────────────
        for line_no, table_name, op in ops:
            func_qname = _resolve(spans, line_no) or fallback_qname
            if not func_qname:
                continue

            # Apply schema prefix fallback: unqualified names → ctx_schema.NAME
            full_tbl_name = (
                f"{ctx_schema}.{table_name}"
                if ctx_schema and "." not in table_name
                else table_name
            )
            tbl_qname = context.table_qname(full_tbl_name)
            _add_unique(result, GraphNode(
                label=S.LABEL_TABLE,
                key="qualified_name",
                key_value=tbl_qname,
                properties={
                    "qualified_name": tbl_qname,
                    "name": table_name,
                    "schema": ctx_schema or None,
                    "repository": repository,
                    "db_name": context.db_name,
                    "layer": "data",
                },
            ))
            rel = S.REL_WRITES_TO if op in ("INSERT", "UPDATE", "DELETE", "MERGE") else S.REL_READS_FROM
            result.edges.append(GraphEdge(
                from_label=S.LABEL_FUNCTION, from_key="qualified_name", from_key_value=func_qname,
                to_label=S.LABEL_TABLE,     to_key="qualified_name", to_key_value=tbl_qname,
                rel_type=rel,
                properties={"operation": op, "line": line_no + 1, "source_file": file_path},
            ))

        # ── Inter-package CALLS ───────────────────────────────────────────────
        seen_calls: set[tuple[str, str]] = set()
        for m in _PKG_CALL_RE.finditer(text):
            pkg_ref  = m.group(1).upper()
            proc_ref = m.group(2).upper()
            if pkg_ref in _ORACLE_BUILTIN_PKGS or pkg_ref in _PLSQL_KW_PREFIXES:
                continue
            if _skip(pkg_ref) or _skip(proc_ref):
                continue
            if pkg_name and pkg_ref == pkg_name:
                continue
            call_line = _line_of(text, m.start())
            caller_qname = _resolve(spans, call_line) or fallback_qname
            if not caller_qname:
                continue
            target_full  = f"{pkg_ref}.{proc_ref}"
            target_qname = f"{S.LABEL_FUNCTION}:{repository}:{target_full}"
            edge_key = (caller_qname, target_qname)
            if edge_key in seen_calls:
                continue
            seen_calls.add(edge_key)
            result.edges.append(GraphEdge(
                from_label=S.LABEL_FUNCTION, from_key="qualified_name", from_key_value=caller_qname,
                to_label=S.LABEL_FUNCTION,   to_key="qualified_name", to_key_value=target_qname,
                rel_type=S.REL_CALLS,
                properties={"call_type": "package_proc", "line": call_line + 1,
                            "source_file": file_path},
            ))

        # ── Triggers ─────────────────────────────────────────────────────────
        for m in _TRIGGER_RE.finditer(text):
            trg_name = m.group(1).upper()
            fired_on  = _norm(m.group(2))
            if _skip(fired_on):
                continue

            # Model the trigger body as a Function node
            trg_fn_qname = f"{S.LABEL_FUNCTION}:{repository}:{trg_name}"
            _add_unique(result, GraphNode(
                label=S.LABEL_FUNCTION,
                key="qualified_name",
                key_value=trg_fn_qname,
                properties={
                    "qualified_name": trg_fn_qname,
                    "name": trg_name,
                    "service": service,
                    "repository": repository,
                    "source_file": file_path,
                    "proc_type": "TRIGGER",
                    "trigger_on_table": fired_on,
                    "layer": "logic",
                },
            ))

            # The table the trigger fires on — apply schema prefix from context
            full_fired_on = (
                f"{ctx_schema}.{fired_on}"
                if ctx_schema and "." not in fired_on
                else fired_on
            )
            tbl_qname = context.table_qname(full_fired_on)
            _add_unique(result, GraphNode(
                label=S.LABEL_TABLE,
                key="qualified_name",
                key_value=tbl_qname,
                properties={
                    "qualified_name": tbl_qname,
                    "name": fired_on,
                    "schema": ctx_schema or None,
                    "repository": repository,
                    "db_name": context.db_name,
                    "layer": "data",
                },
            ))

            # TRIGGERS edge: trigger function fires ON the table
            result.edges.append(GraphEdge(
                from_label=S.LABEL_FUNCTION, from_key="qualified_name", from_key_value=trg_fn_qname,
                to_label=S.LABEL_TABLE,     to_key="qualified_name", to_key_value=tbl_qname,
                rel_type=S.REL_TRIGGERS,
                properties={"framework": "Oracle Trigger", "source_file": file_path},
            ))

        return result


# ── Helpers ───────────────────────────────────────────────────────────────────

def _norm(name: str) -> str:
    """Strip quotes, schema prefix; uppercase."""
    name = name.strip().strip('"')
    if "." in name:
        name = name.rsplit(".", 1)[-1].strip().strip('"')
    return name.upper()


def _skip(name: str) -> bool:
    if not name or len(name) < 2:
        return True
    if name in _SKIP_NAMES:
        return True
    if bool(_SYS_PREFIX_RE.match(name)):
        return True
    if name.startswith("("):
        return True
    return False


def _line_of(text: str, pos: int) -> int:
    return text[:pos].count("\n")


def _resolve(spans: list[tuple[int, str]], line_no: int) -> str | None:
    """Return the qname of the nearest procedure/function declared at or before line_no."""
    result = None
    for span_line, qname in spans:
        if span_line <= line_no:
            result = qname
        else:
            break
    return result


def _add_unique(result: ExtractionResult, node: GraphNode) -> None:
    if node.key_value not in {n.key_value for n in result.nodes}:
        result.nodes.append(node)


# Mini SQL scanners for EXECUTE IMMEDIATE string content
_MINI_INSERT = re.compile(r"\bINSERT\s+(?:INTO\s+)?(?:[\w$#]+\.)?([\w$#]+)", re.IGNORECASE)
_MINI_UPDATE = re.compile(r"\bUPDATE\s+(?:[\w$#]+\.)?([\w$#]+)\s+SET\b", re.IGNORECASE)
_MINI_DELETE = re.compile(r"\bDELETE\s+FROM\s+(?:[\w$#]+\.)?([\w$#]+)", re.IGNORECASE)
_MINI_MERGE  = re.compile(r"\bMERGE\s+INTO\s+(?:[\w$#]+\.)?([\w$#]+)", re.IGNORECASE)
_MINI_FROM   = re.compile(r"\bFROM\s+(?:[\w$#]+\.)?([\w$#]+)", re.IGNORECASE)


def _tables_from_sql_literal(sql: str) -> list[tuple[str, str]]:
    results = []
    for m in _MINI_INSERT.finditer(sql):
        t = _norm(m.group(1))
        if not _skip(t): results.append((t, "INSERT"))
    for m in _MINI_UPDATE.finditer(sql):
        t = _norm(m.group(1))
        if not _skip(t): results.append((t, "UPDATE"))
    for m in _MINI_DELETE.finditer(sql):
        t = _norm(m.group(1))
        if not _skip(t): results.append((t, "DELETE"))
    for m in _MINI_MERGE.finditer(sql):
        t = _norm(m.group(1))
        if not _skip(t): results.append((t, "MERGE"))
    for m in _MINI_FROM.finditer(sql):
        t = _norm(m.group(1))
        if not _skip(t): results.append((t, "SELECT"))
    return results
