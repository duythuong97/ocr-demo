"""CSV repository-mapping extractor.

Reads a CSV file that maps repository classes (data-access layer) to the
database tables they read from or write to.  This is useful when the project
uses MyBatis XML or similar mappers that cannot be automatically parsed.

Expected columns::

    class_name,service_id,bounded_context,schema,table,operation,description

- ``class_name``      : fully-unqualified repository class name, e.g. ``EmployeeRepository``
- ``service_id``      : service that owns this class, e.g. ``aservice`` / ``bservice`` (optional)
- ``bounded_context`` : domain context, e.g. ``employee`` / ``payroll``
- ``schema``          : database schema, e.g. ``HR``
- ``table``           : table name, e.g. ``EMPLOYEES``
- ``operation``       : SQL operation — SELECT, INSERT, UPDATE, DELETE, MERGE, CALL
- ``description``     : human-readable description of the operation (optional)

The extractor creates:

* One ``ServiceClass`` node per unique ``class_name``.
* One stub ``Table`` node per unique ``schema.table`` (merges with existing Table nodes).
* A ``READS`` edge for SELECT operations.
* A ``WRITES`` edge for INSERT / UPDATE / DELETE / MERGE / UPSERT / CALL operations.

``qualified_name`` patterns
---------------------------
- ServiceClass : ``ServiceClass:{repo_key}:{service_id}:{bounded_context}:{class_name}``
- Table        : ``Table:{repo_key}:{db_name}:{SCHEMA}.{TABLE}``
"""
from __future__ import annotations

import csv
import io
import logging
from pathlib import Path

from graph.extractors.base import BaseExtractor
from graph.db.entities import ExtractionContext, ExtractionResult, GraphNode, GraphEdge
from graph.db import schema as S

logger = logging.getLogger(__name__)

_REQUIRED_COLUMNS = {"class_name", "table"}
_WRITE_OPS = {"INSERT", "UPDATE", "DELETE", "MERGE", "UPSERT", "CALL"}

# operation value → Neo4j relationship type
_OP_TO_REL: dict[str, str] = {
    "SELECT":   "READS_FROM",
    "INSERT":   "INSERTS_INTO",
    "UPDATE":   "UPDATES",
    "DELETE":   "DELETES_FROM",
    "TRUNCATE": "TRUNCATES",
    "MERGE":    "WRITES_TO",
    "UPSERT":   "WRITES_TO",
    "CALL":     "WRITES_TO",   # stored procedure call — treat as write
}


class CsvRepositoryExtractor(BaseExtractor):
    """Extract ServiceClass nodes and READS/WRITES edges from a repository-mapping CSV."""

    def can_handle(self, file_path: str, text: str) -> bool:
        if Path(file_path).suffix.lower() != ".csv":
            return False
        first_line = next(
            (ln.strip() for ln in text.splitlines() if ln.strip()), ""
        )
        cols = {c.strip().lower() for c in first_line.split(",")}
        return _REQUIRED_COLUMNS.issubset(cols)

    def extract(
        self,
        file_path: str,
        text: str,
        context: ExtractionContext,
    ) -> ExtractionResult:
        result = ExtractionResult(
            source_file=file_path, extractor_name="CsvRepositoryExtractor"
        )
        repo_key = context.repository
        db_name = context.db_name or repo_key
        # schema_db_map: {SCHEMA -> db_name} from sources.yaml metadata
        schema_db_map: dict[str, str] = context.extra_tags.get("schema_db_map", {})

        try:
            reader = csv.DictReader(io.StringIO(text))
        except Exception as exc:
            logger.warning("[CsvRepositoryExtractor] Could not parse %s: %s", file_path, exc)
            return result

        # Track created nodes to avoid duplicates within the same file
        seen_classes: dict[str, GraphNode] = {}   # key → node
        seen_tables: dict[str, GraphNode] = {}    # key → node

        for row in reader:
            norm = {k.strip().lower(): (v or "").strip() for k, v in row.items()}
            class_name = norm.get("class_name", "").strip()
            service_id = norm.get("service_id", "").strip().lower()
            bounded_context = norm.get("bounded_context", "").strip().lower()
            schema = norm.get("schema", "").upper()
            table = norm.get("table", "").upper()
            operation = norm.get("operation", "SELECT").upper()
            description = norm.get("description", "")

            if not class_name or not table:
                continue

            # ── ServiceClass node ──────────────────────────────────────────────
            svc_qn = (
                f"{S.LABEL_SERVICE_CLASS}:{repo_key}:{service_id}:{bounded_context}:{class_name}"
                if service_id
                else f"{S.LABEL_SERVICE_CLASS}:{repo_key}:{bounded_context}:{class_name}"
            )
            if svc_qn not in seen_classes:
                svc_node = GraphNode(
                    label=S.LABEL_SERVICE_CLASS,
                    key="qualified_name",
                    key_value=svc_qn,
                    properties={
                        "qualified_name": svc_qn,
                        "name": class_name,
                        "service_id": service_id or None,
                        "bounded_context": bounded_context,
                        "repository": repo_key,
                        "source_type": "manual",
                        "confidence": 0.8,
                        "layer": "logic",
                    },
                )
                seen_classes[svc_qn] = svc_node
                result.nodes.append(svc_node)

            # ── Table stub node ────────────────────────────────────────────────
            # Resolve db_name: prefer schema_db_map lookup, then context.db_name, then repo_key
            # Table identity: (db_name, schema.name) — repo_key NOT included
            resolved_db = schema_db_map.get(schema) or db_name
            table_qn = (
                f"{S.LABEL_TABLE}:{resolved_db}:{schema}.{table}"
                if schema
                else f"{S.LABEL_TABLE}:{resolved_db}:{table}"
            )
            if table_qn not in seen_tables:
                table_node = GraphNode(
                    label=S.LABEL_TABLE,
                    key="qualified_name",
                    key_value=table_qn,
                    properties={
                        "qualified_name": table_qn,
                        "name": table,
                        "schema": schema,
                        "db_name": resolved_db,
                        "repository": repo_key,
                        "source_type": "manual",
                        "confidence": 0.8,
                        "layer": "data",
                    },
                )
                seen_tables[table_qn] = table_node
                result.nodes.append(table_node)

            # ── Edge — granular per DML operation ────────────────────────────
            rel_type = _OP_TO_REL.get(operation, S.REL_WRITES_TO if operation in _WRITE_OPS else S.REL_READS_FROM)
            edge_props: dict = {
                "operation": operation,
                "source_type": "manual",
                "confidence": 0.8,
            }
            if description:
                edge_props["description"] = description

            result.edges.append(
                GraphEdge(
                    from_label=S.LABEL_SERVICE_CLASS,
                    from_key="qualified_name",
                    from_key_value=svc_qn,
                    to_label=S.LABEL_TABLE,
                    to_key="qualified_name",
                    to_key_value=table_qn,
                    rel_type=rel_type,
                    properties=edge_props,
                )
            )

        logger.debug(
            "[CsvRepositoryExtractor] %s → %d classes, %d tables, %d edges",
            file_path,
            len(seen_classes),
            len(seen_tables),
            len(result.edges),
        )
        return result
