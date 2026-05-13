"""KnowledgeService: persist and apply manual knowledge to Neo4j / Solr / Qdrant.

Manual knowledge survives ingest re-runs because apply_all() is called AFTER
every ingest job completes — so APOC MERGE re-asserts manual nodes/edges on top
of whatever the extractor produced.

Three knowledge types:
  knowledge_nodes  → merged into Neo4j as labelled nodes
  knowledge_edges  → merged into Neo4j as typed relationships
  knowledge_texts  → indexed into Solr + Qdrant with synthetic file_path
"""
from __future__ import annotations

import config as cfg
import csv
import io
import json
import logging
from typing import Any

from infra.solr_store import SolrStore
from ingest.store import IndexingStateStore

logger = logging.getLogger("indexing")

_KNOWLEDGE_SOURCE_PREFIX = "__knowledge__"

# ── Pre-defined relationship templates ────────────────────────────────────────
# User picks a template → fills in simple names → backend builds qualified_name.
KNOWLEDGE_TEMPLATES: list[dict] = [
    # Database
    {"id": "func_writes_table",   "category": "Database",     "icon": "✏️",  "label": "Function writes to Table",       "from_label": "Function",      "from_hint": "Function or method name",   "rel_type": "WRITES_TO",    "to_label": "Table",         "to_hint": "Table name"},
    {"id": "func_reads_table",    "category": "Database",     "icon": "📖", "label": "Function reads from Table",      "from_label": "Function",      "from_hint": "Function or method name",   "rel_type": "READS_FROM",   "to_label": "Table",         "to_hint": "Table name"},
    {"id": "func_writes_view",    "category": "Database",     "icon": "✏️",  "label": "Function writes to View",        "from_label": "Function",      "from_hint": "Function or method name",   "rel_type": "WRITES_TO",    "to_label": "View",          "to_hint": "View name"},
    {"id": "func_reads_view",     "category": "Database",     "icon": "📖", "label": "Function reads from View",       "from_label": "Function",      "from_hint": "Function or method name",   "rel_type": "READS_FROM",   "to_label": "View",          "to_hint": "View name"},
    # Code Structure
    {"id": "func_calls_func",     "category": "Code",         "icon": "⚡",  "label": "Function calls Function",        "from_label": "Function",      "from_hint": "Caller function name",      "rel_type": "CALLS",        "to_label": "Function",      "to_hint": "Callee function name"},
    {"id": "func_belongs_class",  "category": "Code",         "icon": "🏠", "label": "Function belongs to Class",      "from_label": "Function",      "from_hint": "Method name",               "rel_type": "BELONGS_TO",   "to_label": "Class",         "to_hint": "Class name"},
    {"id": "func_in_job",         "category": "Code",         "icon": "⏰", "label": "Function runs in Background Job","from_label": "Function",      "from_hint": "Function name",             "rel_type": "BELONGS_TO",   "to_label": "BackgroundJob", "to_hint": "Job name"},
    {"id": "class_uses_mapper",   "category": "Code",         "icon": "🗺️", "label": "Repository uses XML Mapper",     "from_label": "Class",         "from_hint": "Repository class name",     "rel_type": "HAS_MAPPER",   "to_label": "Module",        "to_hint": "XML mapper name (e.g. UserMapper)"},
    # Integration
    {"id": "func_calls_api",      "category": "Integration",  "icon": "🌐", "label": "Function calls external API",    "from_label": "Function",      "from_hint": "Function name",             "rel_type": "CALLS_API",    "to_label": "ApiCall",       "to_hint": "API path or URL"},
    {"id": "func_publishes",      "category": "Integration",  "icon": "📢", "label": "Function publishes Event",       "from_label": "Function",      "from_hint": "Publisher function name",   "rel_type": "PUBLISHES",    "to_label": "EventTopic",    "to_hint": "Event / topic name"},
    # Event-Driven
    {"id": "class_subscribes",    "category": "Events",       "icon": "📥", "label": "Class subscribes to Event",      "from_label": "Class",         "from_hint": "Consumer class name",       "rel_type": "SUBSCRIBES",   "to_label": "EventTopic",    "to_hint": "Event / topic name"},
    {"id": "func_instantiates",   "category": "Events",       "icon": "🔨", "label": "Function instantiates Class",    "from_label": "Function",      "from_hint": "Factory function name",     "rel_type": "INSTANTIATES", "to_label": "Class",         "to_hint": "Class being instantiated"},
    # Workflow / scheduling
    {"id": "workflow_executes_func", "category": "Workflow",    "icon": "⚙️",  "label": "Workflow/Job executes Function",  "from_label": "Workflow",      "from_hint": "JP1 Jobnet / Airflow DAG name", "rel_type": "EXECUTES",   "to_label": "Function",      "to_hint": "Function or script name"},
    {"id": "task_depends_on_task",   "category": "Workflow",    "icon": "🔗", "label": "Task depends on Task",           "from_label": "Task",          "from_hint": "Downstream task name",         "rel_type": "DEPENDS_ON", "to_label": "Task",          "to_hint": "Upstream task name"},
    # Documentation
    {"id": "func_documented_by",     "category": "Documentation", "icon": "📄", "label": "Function documented by Document", "from_label": "Function",     "from_hint": "Function name",                "rel_type": "DOCUMENTED_BY", "to_label": "Document",   "to_hint": "Document title or key"},
    {"id": "table_defined_by",       "category": "Documentation", "icon": "📄", "label": "Table defined by Document",      "from_label": "Table",        "from_hint": "Table name",                   "rel_type": "DEFINED_BY",    "to_label": "Document",   "to_hint": "Schema doc title or key"},
]

_TEMPLATE_BY_ID: dict[str, dict] = {t["id"]: t for t in KNOWLEDGE_TEMPLATES}

# Valid Neo4j labels accepted in CSV import
_VALID_LABELS = {
    "Function", "Class", "Table", "View", "Module",
    "BackgroundJob", "ApiCall", "EventTopic",
    # Workflow / scheduling
    "Workflow", "Task", "TaskStep",
    # Documentation
    "Document",
    # Infrastructure
    "Repository", "Service", "ApiEndpoint", "ExternalService", "File",
}

_CSV_HEADERS = ["from_label", "from_name", "repository", "rel_type", "to_label", "to_name"]


class KnowledgeService:
    def __init__(
        self,
        store: IndexingStateStore,
        graph_client: Any | None,         # GraphClient or None
        solr: SolrStore | None,           # SolrStore or None
        ingest_service: Any | None,       # QdrantStore or None
    ) -> None:
        self._store = store
        self._graph = graph_client
        self._solr_store = solr
        self._ingest = ingest_service

    # ── Graph nodes ────────────────────────────────────────────────────────────

    def list_nodes(self, label: str = "", q: str = "", limit: int = 200) -> list[dict]:
        return self._store.list_knowledge_nodes(label=label, q=q, limit=limit)

    def create_node(self, label: str, name: str, repository: str, properties: dict) -> dict:
        node_id = self._store.create_knowledge_node(label, name, repository, properties)
        return {"id": node_id, "label": label, "name": name, "repository": repository}

    def update_node(self, node_id: int, name: str, repository: str, properties: dict) -> bool:
        return self._store.update_knowledge_node(node_id, name, repository, properties)

    def delete_node(self, node_id: int) -> bool:
        return self._store.delete_knowledge_node(node_id)

    # ── Graph edges ────────────────────────────────────────────────────────────

    def list_edges(self, rel_type: str = "", q: str = "", limit: int = 200) -> list[dict]:
        return self._store.list_knowledge_edges(rel_type=rel_type, q=q, limit=limit)

    def create_edge(self, from_qname: str, to_qname: str, rel_type: str, properties: dict) -> dict:
        edge_id = self._store.create_knowledge_edge(from_qname, to_qname, rel_type, properties)
        return {"id": edge_id, "from_qname": from_qname, "to_qname": to_qname, "rel_type": rel_type}

    def delete_edge(self, edge_id: int) -> bool:
        return self._store.delete_knowledge_edge(edge_id)

    # ── Text knowledge ─────────────────────────────────────────────────────────

    def list_texts(self, q: str = "", tags: str = "", limit: int = 200) -> list[dict]:
        return self._store.list_knowledge_texts(q=q, tags=tags, limit=limit)

    def get_text(self, text_id: int) -> dict | None:
        return self._store.get_knowledge_text(text_id)

    def create_text(self, title: str, content: str, tags: str, repository: str) -> dict:
        text_id = self._store.create_knowledge_text(title, content, tags, repository)
        self._index_text(text_id, title, content, tags, repository)
        return {"id": text_id, "title": title}

    def update_text(self, text_id: int, title: str, content: str, tags: str, repository: str) -> bool:
        ok = self._store.update_knowledge_text(text_id, title, content, tags, repository)
        if ok:
            self._index_text(text_id, title, content, tags, repository)
        return ok

    def delete_text(self, text_id: int) -> bool:
        row = self._store.get_knowledge_text(text_id)
        if not row:
            return False
        self._remove_text_from_search(text_id)
        return self._store.delete_knowledge_text(text_id)

    # ── Template helpers ───────────────────────────────────────────────────────

    @staticmethod
    def get_templates() -> list[dict]:
        return KNOWLEDGE_TEMPLATES

    def quick_add(
        self,
        template_id: str,
        from_name: str,
        to_name: str,
        repository: str = "manual",
        properties: dict | None = None,
    ) -> dict:
        """Create from-node + to-node + edge in one call using a pre-defined template.

        The user only provides simple names; qualified_name is built internally.
        """
        tpl = _TEMPLATE_BY_ID.get(template_id)
        if not tpl:
            raise ValueError(f"Unknown template id: {template_id!r}")

        repo = (repository or "manual").strip()
        from_name = from_name.strip()
        to_name = to_name.strip()
        if not from_name or not to_name:
            raise ValueError("from_name and to_name are required")

        from_label = tpl["from_label"]
        to_label = tpl["to_label"]
        rel_type = tpl["rel_type"]

        from_id = self._store.upsert_knowledge_node(from_label, from_name, repo)
        to_id = self._store.upsert_knowledge_node(to_label, to_name, repo)

        from_qname = f"{from_label}:{repo}:{from_name}"
        to_qname = f"{to_label}:{repo}:{to_name}"

        edge_id = self._store.create_knowledge_edge(from_qname, to_qname, rel_type, properties or {})

        return {
            "from_id": from_id,
            "to_id": to_id,
            "edge_id": edge_id,
            "from_qname": from_qname,
            "to_qname": to_qname,
            "rel_type": rel_type,
        }

    def import_csv(self, file_bytes: bytes) -> dict:
        """Bulk-import edges from a CSV file.

        Expected columns (order insensitive):
            from_label, from_name, repository, rel_type, to_label, to_name

        Returns: {"created": N, "skipped": N, "errors": [...]}
        """
        text = file_bytes.decode("utf-8-sig", errors="replace")
        reader = csv.DictReader(io.StringIO(text))

        # Normalise header names (lowercase, strip spaces)
        if reader.fieldnames is None:
            return {"created": 0, "skipped": 0, "errors": ["Empty or unreadable CSV"]}

        created = 0
        skipped = 0
        errors: list[str] = []

        for row_num, raw_row in enumerate(reader, start=2):
            row = {k.strip().lower(): (v or "").strip() for k, v in raw_row.items()}

            from_label = row.get("from_label", "").strip()
            from_name = row.get("from_name", "").strip()
            to_label = row.get("to_label", "").strip()
            to_name = row.get("to_name", "").strip()
            rel_type = (row.get("rel_type", "") or "").strip().upper()
            repository = (row.get("repository", "") or "manual").strip() or "manual"

            # Skip blank rows
            if not any([from_label, from_name, to_label, to_name, rel_type]):
                skipped += 1
                continue

            # Validate
            row_errors = []
            if not from_label:
                row_errors.append("from_label missing")
            elif from_label not in _VALID_LABELS:
                row_errors.append(f"from_label '{from_label}' not in {sorted(_VALID_LABELS)}")
            if not from_name:
                row_errors.append("from_name missing")
            if not to_label:
                row_errors.append("to_label missing")
            elif to_label not in _VALID_LABELS:
                row_errors.append(f"to_label '{to_label}' not in {sorted(_VALID_LABELS)}")
            if not to_name:
                row_errors.append("to_name missing")
            if not rel_type:
                row_errors.append("rel_type missing")

            if row_errors:
                errors.append(f"Row {row_num}: {'; '.join(row_errors)}")
                skipped += 1
                continue

            try:
                self._store.upsert_knowledge_node(from_label, from_name, repository)
                self._store.upsert_knowledge_node(to_label, to_name, repository)
                from_qname = f"{from_label}:{repository}:{from_name}"
                to_qname = f"{to_label}:{repository}:{to_name}"
                self._store.create_knowledge_edge(from_qname, to_qname, rel_type, {})
                created += 1
            except Exception as exc:
                errors.append(f"Row {row_num}: {exc}")
                skipped += 1

        return {"created": created, "skipped": skipped, "errors": errors}

    # ── Apply ──────────────────────────────────────────────────────────────────

    def apply_all(self) -> dict[str, Any]:
        graph_result = self.apply_graph()
        text_result = self.apply_texts()
        return {"graph": graph_result, "texts": text_result}

    def apply_graph(self) -> dict[str, Any]:
        if self._graph is None or not getattr(self._graph, "available", False):
            return {"skipped": True, "reason": "Neo4j not available"}

        nodes = self._store.list_knowledge_nodes(limit=10000)
        edges = self._store.list_knowledge_edges(limit=10000)

        nodes_written = 0
        edges_written = 0

        for n in nodes:
            label = n["label"]
            name = n["name"]
            repository = n["repository"]
            props = json.loads(n["properties_json"] or "{}")
            qname = f"{label}:{repository}:{name}"
            props.update({
                "qualified_name": qname,
                "name": name,
                "repository": repository,
                "source": "manual",
            })
            try:
                self._graph.run_write(
                    """
                    CALL apoc.merge.node([$label], {qualified_name: $qname}, $props, $props)
                    YIELD node RETURN count(node)
                    """,
                    {"label": label, "qname": qname, "props": props},
                )
                nodes_written += 1
            except Exception as exc:
                logger.warning("Knowledge node MERGE failed (%s): %s", qname, exc)

        for e in edges:
            try:
                props = json.loads(e["properties_json"] or "{}")
                self._graph.run_write(
                    """
                    MATCH (a {qualified_name: $from_qname})
                    MATCH (b {qualified_name: $to_qname})
                    CALL apoc.merge.relationship(a, $rel_type, {}, $props, b, $props)
                    YIELD rel RETURN count(rel)
                    """,
                    {
                        "from_qname": e["from_qname"],
                        "to_qname": e["to_qname"],
                        "rel_type": e["rel_type"],
                        "props": props,
                    },
                )
                edges_written += 1
            except Exception as exc:
                logger.warning("Knowledge edge MERGE failed (%s→%s): %s", e["from_qname"], e["to_qname"], exc)

        return {"nodes_written": nodes_written, "edges_written": edges_written}

    def apply_texts(self) -> dict[str, Any]:
        rows = self._store.list_knowledge_texts(limit=10000)
        indexed = 0
        for row in rows:
            text_id = row["id"]
            full = self._store.get_knowledge_text(text_id)
            if not full:
                continue
            try:
                self._index_text(text_id, full["title"], full["content"], full["tags"], full["repository"])
                indexed += 1
            except Exception as exc:
                logger.warning("Knowledge text re-index failed (id=%d): %s", text_id, exc)
        return {"texts_indexed": indexed}

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _synthetic_path(self, text_id: int) -> str:
        return f"{_KNOWLEDGE_SOURCE_PREFIX}/{text_id}"

    def _index_text(self, text_id: int, title: str, content: str, tags: str, repository: str) -> None:
        file_path = self._synthetic_path(text_id)
        full_text = f"{title}\n\n{content}"
        if tags:
            full_text = f"[Tags: {tags}]\n{full_text}"

        if self._solr_store is not None:
            try:
                doc = {
                    cfg.FIELD_ID: f"knowledge-{text_id}",
                    cfg.FIELD_TITLE: title,
                    cfg.FIELD_CONTENT: full_text,
                    cfg.FIELD_FILE_TYPE: "knowledge",
                    cfg.FIELD_FILE_PATH: file_path,
                    cfg.FIELD_REPOSITORY: repository,
                    cfg.FIELD_REPOSITORY_PATH: "",
                }
                self._solr_store.index_doc(doc)
            except Exception as exc:
                logger.warning("Knowledge Solr index failed (id=%d): %s", text_id, exc)

        if self._ingest is not None:
            try:
                self._ingest.upsert_file(
                    file_path=file_path,
                    rel_path=f"knowledge/{text_id}",
                    text=full_text,
                    repository=repository,
                    file_type="knowledge",
                    repository_url_base="",
                )
            except Exception as exc:
                logger.warning("Knowledge Qdrant index failed (id=%d): %s", text_id, exc)

        self._store.mark_knowledge_text_indexed(text_id)

    def _remove_text_from_search(self, text_id: int) -> None:
        if self._solr_store is not None:
            try:
                self._solr_store.delete_by_id(f"knowledge-{text_id}")
            except Exception as exc:
                logger.warning("Knowledge Solr delete failed (id=%d): %s", text_id, exc)

        if self._ingest is not None:
            try:
                file_path = self._synthetic_path(text_id)
                self._ingest.delete_file(file_path)
            except Exception as exc:
                logger.warning("Knowledge Qdrant delete failed (id=%d): %s", text_id, exc)
