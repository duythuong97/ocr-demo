"""Language-agnostic dataclasses for graph extraction results.

Every extractor returns ExtractionResult containing lists of GraphNode and
GraphEdge.  These are plain data — no DB coupling.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class GraphNode:
    """A node to be MERGE'd into Neo4j.

    label       : Neo4j label, e.g. "Table", "Function", "ApiEndpoint"
    key         : property used as unique identifier within this label
    key_value   : value of that property
    properties  : additional properties to set/update on MERGE
    source      : "extracted" | "manual" | "rule"
    """
    label: str
    key: str
    key_value: str
    properties: dict[str, Any] = field(default_factory=dict)
    source: str = "extracted"

    def identity(self) -> tuple[str, str, str]:
        return (self.label, self.key, self.key_value)


@dataclass
class GraphEdge:
    """A relationship to be MERGE'd into Neo4j.

    from_label / from_key / from_key_value  : identifies the source node
    to_label   / to_key   / to_key_value    : identifies the target node
    rel_type    : Neo4j relationship type, e.g. "WRITES_TO", "READS_FROM"
    properties  : additional properties on the relationship
    """
    from_label: str
    from_key: str
    from_key_value: str
    to_label: str
    to_key: str
    to_key_value: str
    rel_type: str
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExtractionResult:
    """Output of a single extractor run on one file."""
    nodes: list[GraphNode] = field(default_factory=list)
    edges: list[GraphEdge] = field(default_factory=list)
    source_file: str = ""
    extractor_name: str = ""

    def merge(self, other: "ExtractionResult") -> "ExtractionResult":
        return ExtractionResult(
            nodes=self.nodes + other.nodes,
            edges=self.edges + other.edges,
            source_file=self.source_file,
            extractor_name=f"{self.extractor_name}+{other.extractor_name}",
        )

    def is_empty(self) -> bool:
        return not self.nodes and not self.edges


@dataclass
class ExtractionContext:
    """Shared context passed to every extractor for a given file.

    Provides repository metadata and configurable naming rules so extractors
    can infer service / domain names without hardcoding.
    """
    repository: str = ""
    repository_path: str = ""
    domain: str = ""
    service_name: str = ""
    # C#: strip this prefix from namespace to derive service name
    # e.g. "Company.Orders" → service "orders"
    namespace_prefix: str = ""
    # Extra key-value tags propagated to every node created in this context
    extra_tags: dict[str, Any] = field(default_factory=dict)

    # VCS / repository metadata
    source: str = "git"       # "git" | "svn"
    vcs_url: str = ""
    repo_owner: str = ""
    team_name: str = ""

    # Database scoping — set when a source folder belongs to a specific DB schema.
    # Used to build table qnames so that same-named tables in different DBs are distinct.
    # e.g. db_name="OracleDB_Main" → Table:MyRepo:OracleDB_Main:USERS
    # Leave empty for single-DB repos (falls back to Table:MyRepo:USERS)
    db_name: str = ""

    # Workflow / scheduling metadata (JP1, Airflow, cron, Hangfire …)
    workflow_name: str = ""   # JP1 Jobnet name / Airflow DAG ID
    workflow_id: str = ""     # External scheduler ID
    scheduler_type: str = ""  # "jp1" | "airflow" | "cron" | "hangfire"

    def repo_qname(self) -> str:
        """Return the qualified_name used for the Repository node."""
        src = self.source or "git"
        return f"Repository:{src}:{self.repository}"

    def table_qname(self, table_name: str) -> str:
        """Return the qualified_name for a Table node.

        Identity is (db_name, table_name) — repository is NOT included so that
        the same physical table referenced from different repos merges cleanly.
          With db_name:    Table:{db_name}:{table_name}
          Without db_name: Table:{table_name}
        """
        if self.db_name:
            return f"Table:{self.db_name}:{table_name}"
        return f"Table:{table_name}"

    def infer_service_from_namespace(self, namespace: str) -> str:
        """Strip namespace_prefix and return the next segment as service name."""
        if self.service_name:
            return self.service_name
        if self.namespace_prefix and namespace.startswith(self.namespace_prefix):
            remainder = namespace[len(self.namespace_prefix):].lstrip(".")
            return remainder.split(".")[0].lower() if remainder else self.repository
        return self.repository

    def infer_service_from_path(self, file_path: str) -> str:
        """Derive service name from folder structure when namespace is unavailable."""
        if self.service_name:
            return self.service_name
        parts = Path(file_path).parts
        # Use repository root folder name or first meaningful segment
        for part in parts:
            if part not in {"src", "lib", "app", "main", "java", "cs", "ts", ".", ".."}:
                return part.lower()
        return self.repository
