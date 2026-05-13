"""SQL*Plus / Oracle runner script extractor.

Handles .sql files that contain SQL*Plus commands (runner / migration scripts)
rather than DDL or PL/SQL CREATE blocks.

Detects:
  - EXEC / EXECUTE [schema.]pkg.proc([args])   → CALLS edge to PL/SQL Function
  - CALL [schema.]pkg.proc([args])              → CALLS edge to PL/SQL Function
  - @ script.sql / @@ script.sql               → REFERENCES edge (script includes)
  - Anonymous PL/SQL blocks (BEGIN...END)       → treated as an inline runner

Creates:
  - Function node for the script itself  (proc_type="SQLPLUS_SCRIPT")
  - Stub Function nodes for called procs (deferred — linked by Neo4j merge)
  - CALLS edges: script → target proc
  - REFERENCES edges: script → included script  (file_path as key)

File detection: .sql files that do NOT begin with a CREATE OR REPLACE block
but DO contain EXEC/CALL/@ patterns.
"""
from __future__ import annotations

import re
import logging
from pathlib import Path

from graph.extractors.base import BaseExtractor
from graph.db.entities import ExtractionContext, ExtractionResult, GraphEdge, GraphNode
from graph.db import schema as S

logger = logging.getLogger(__name__)

_SQL_EXTENSION = ".sql"

# Quick accept: must have at least one of these SQL*Plus commands
_HAS_SQLPLUS = re.compile(
    r"^\s*(?:EXEC(?:UTE)?|CALL|@@?)\s",
    re.IGNORECASE | re.MULTILINE,
)
# Quick reject: file starts with a CREATE block → handled by oracle_plsql / oracle_ddl
_HAS_CREATE = re.compile(
    r"\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:TABLE|VIEW|PACKAGE|PROCEDURE|FUNCTION|TRIGGER|SEQUENCE|INDEX)\b",
    re.IGNORECASE,
)

# EXEC[UTE] [schema.]pkg.proc[(args)]
_EXEC_RE = re.compile(
    r"^\s*EXEC(?:UTE)?\s+"
    r'(?:"?[\w$#]+"?\s*\.\s*)?'     # optional schema prefix
    r'"?([\w$#]+)"?'                  # package or standalone proc
    r'(?:\."?([\w$#]+)"?)?'          # optional .proc_name
    r'\s*(?:\([^;]{0,400}\))?',       # optional arg list
    re.IGNORECASE | re.MULTILINE,
)

# CALL [schema.]pkg.proc(args)
_CALL_RE = re.compile(
    r"^\s*CALL\s+"
    r'(?:"?[\w$#]+"?\s*\.\s*)?'
    r'"?([\w$#]+)"?'
    r'(?:\."?([\w$#]+)"?)?'
    r'\s*\(',
    re.IGNORECASE | re.MULTILINE,
)

# @script.sql or @@script.sql
_INCLUDE_RE = re.compile(
    r"^\s*@@?([\w/\\\.\-]+\.sql)\b",
    re.IGNORECASE | re.MULTILINE,
)

# Oracle built-ins / system objects to skip
_SKIP_NAMES: frozenset[str] = frozenset({
    "DUAL", "ROWNUM", "DBMS_OUTPUT", "DBMS_SQL", "DBMS_LOB", "DBMS_UTILITY",
    "DBMS_LOCK", "DBMS_SCHEDULER", "DBMS_JOB", "UTL_FILE", "UTL_HTTP",
    "APEX_UTIL", "APEX_APPLICATION", "SYS", "STANDARD",
})


def _norm(name: str) -> str:
    return name.strip().strip('"').upper()


def _skip(name: str) -> bool:
    if not name or len(name) < 2:
        return True
    return name.upper() in _SKIP_NAMES


class SqlPlusScriptExtractor(BaseExtractor):
    """Extracts EXEC/CALL stored-proc calls from SQL*Plus runner scripts."""

    def can_handle(self, file_path: str, text: str) -> bool:
        if Path(file_path).suffix.lower() != _SQL_EXTENSION:
            return False
        # Skip files that are DDL / PL/SQL CREATE blocks (other extractors handle those)
        if _HAS_CREATE.search(text[:5_000]):
            return False
        return bool(_HAS_SQLPLUS.search(text))

    def extract(self, file_path: str, text: str, context: ExtractionContext) -> ExtractionResult:
        result = ExtractionResult(source_file=file_path, extractor_name="SqlPlusScriptExtractor")
        repository = context.repository
        service = context.service_name or context.infer_service_from_path(file_path)
        stem = Path(file_path).stem.upper()

        # Script itself is modelled as a Function node (runner/script)
        script_qname = f"{S.LABEL_FUNCTION}:{repository}:{stem}"
        result.nodes.append(GraphNode(
            label=S.LABEL_FUNCTION,
            key="qualified_name",
            key_value=script_qname,
            properties={
                "qualified_name": script_qname,
                "name": stem,
                "service": service,
                "repository": repository,
                "source_file": file_path,
                "proc_type": "SQLPLUS_SCRIPT",
            },
        ))

        seen_calls: set[str] = set()

        def _add_call(pkg: str, proc: str | None, line: int) -> None:
            pkg_u = _norm(pkg)
            if _skip(pkg_u):
                return
            if proc:
                proc_u = _norm(proc)
                full   = f"{pkg_u}.{proc_u}"
            else:
                full = pkg_u
            target_qname = f"{S.LABEL_FUNCTION}:{repository}:{full}"
            if target_qname in seen_calls:
                return
            seen_calls.add(target_qname)
            result.edges.append(GraphEdge(
                from_label=S.LABEL_FUNCTION, from_key="qualified_name", from_key_value=script_qname,
                to_label=S.LABEL_FUNCTION,   to_key="qualified_name", to_key_value=target_qname,
                rel_type=S.REL_CALLS,
                properties={"call_type": "EXEC", "line": line, "source_file": file_path},
            ))

        # ── EXEC / EXECUTE statements ─────────────────────────────────────────
        for m in _EXEC_RE.finditer(text):
            line = text[:m.start()].count("\n") + 1
            part1 = m.group(1)
            part2 = m.group(2)
            _add_call(part1, part2, line)

        # ── CALL statements ───────────────────────────────────────────────────
        for m in _CALL_RE.finditer(text):
            line = text[:m.start()].count("\n") + 1
            part1 = m.group(1)
            part2 = m.group(2)
            _add_call(part1, part2, line)

        # ── @include / @@include ──────────────────────────────────────────────
        for m in _INCLUDE_RE.finditer(text):
            inc_path = m.group(1)
            inc_qname = f"File:{repository}:{inc_path}"
            result.nodes.append(GraphNode(
                label=S.LABEL_FILE,
                key="qualified_name",
                key_value=inc_qname,
                properties={
                    "qualified_name": inc_qname,
                    "name": Path(inc_path).name,
                    "repository": repository,
                    "file_path": inc_path,
                },
            ))
            result.edges.append(GraphEdge(
                from_label=S.LABEL_FUNCTION, from_key="qualified_name", from_key_value=script_qname,
                to_label=S.LABEL_FILE,       to_key="qualified_name", to_key_value=inc_qname,
                rel_type=S.REL_REFERENCES,
                properties={"include_path": inc_path, "source_file": file_path},
            ))

        return result
