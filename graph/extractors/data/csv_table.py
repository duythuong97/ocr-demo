"""CSV table-list extractor.

Reads a CSV file with the columns::

    scheme,name,db_name,description

and creates ``Table`` nodes directly, without parsing DDL.
This is useful when DDL is not available but a table inventory is exported as CSV.

The header line is detected automatically (case-insensitive).  Any extra columns
beyond the above are stored as ``extra_*`` properties on the node.

The ``db_name`` column is optional; if omitted, the value from ``context.db_name``
is used as fallback.

Example file::

    scheme,name,db_name,description
    HR,EMPLOYEES,OracleHRDB,Main employee master table
    HR,DEPARTMENTS,OracleHRDB,Department reference
    PAYROLL,SALARY_RUNS,OraclePayrollDB,Monthly payroll run header
"""
from __future__ import annotations

import csv
import io
import logging
from pathlib import Path

from graph.extractors.base import BaseExtractor
from graph.db.entities import ExtractionContext, ExtractionResult, GraphEdge, GraphNode
from graph.db import schema as S

logger = logging.getLogger(__name__)

_REQUIRED_COLUMNS = {"scheme", "name"}


class CsvTableExtractor(BaseExtractor):
    """Extract Table nodes from a CSV inventory file.

    ``can_handle`` returns True only when:
    - extension is ``.csv``
    - first non-blank line is a header that contains both ``scheme`` and ``name``
    """

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
            source_file=file_path, extractor_name="CsvTableExtractor"
        )
        repository = context.repository
        db_name = context.db_name or repository

        try:
            reader = csv.DictReader(io.StringIO(text))
        except Exception as exc:
            logger.warning("[CsvTableExtractor] Could not parse %s: %s", file_path, exc)
            return result

        # Normalize header keys to lowercase
        for row in reader:
            norm = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k is not None}
            schema = norm.get("scheme", "").upper()
            name = norm.get("name", "").upper()
            description = norm.get("description", "")
            row_db_name = norm.get("db_name", "").strip() or db_name

            if not name:
                continue

            # Table identity: (db_name, schema.name) — repository NOT included so same
            # physical table merges across repos (manual_data, plsql_hr_repo, etc.)
            qualified_name = f"{S.LABEL_TABLE}:{row_db_name}:{schema}.{name}" if schema else f"{S.LABEL_TABLE}:{row_db_name}:{name}"

            props: dict = {
                "qualified_name": qualified_name,
                "name": name,
                "schema": schema,
                "db_name": row_db_name,
                "repository": repository,
                "description": description,
                "source_file": file_path,
                "source": "csv_inventory",
                "layer": "data",
            }

            # Store any extra columns as extra_* properties
            known = {"scheme", "name", "db_name", "description"}
            for k, v in norm.items():
                if k not in known and v:
                    props[f"extra_{k}"] = v

            result.nodes.append(
                GraphNode(
                    label=S.LABEL_TABLE,
                    key="qualified_name",
                    key_value=qualified_name,
                    properties=props,
                )
            )

            # (Database)-[:CONTAINS]->(Table) edge (cross-project: landscape → manual_data)
            if row_db_name:
                db_qname = f"Database:{row_db_name}"
                result.edges.append(
                    GraphEdge(
                        from_label="Database",
                        from_key="qualified_name",
                        from_key_value=db_qname,
                        to_label=S.LABEL_TABLE,
                        to_key="qualified_name",
                        to_key_value=qualified_name,
                        rel_type=S.REL_CONTAINS,
                        properties={"source": "csv_inventory"},
                    )
                )

        logger.debug(
            "[CsvTableExtractor] %s → %d table nodes", file_path, len(result.nodes)
        )
        return result
