"""GraphService — high-level graph query service wrapping GraphClient.

All Neo4j interaction is centralised here.  Routes and tools import
``services.graph_service`` and never touch ``GraphClient`` directly.

Improved neighbor lookup uses a single, comprehensive Cypher query that
covers:
  • exact qualified_name match          "Table:repo:EMPLOYEES"
  • colon-suffix match                  "…:EMPLOYEES"
  • dot-suffix match (SQL schema)       "HR.EMPLOYEES" → ENDS WITH ".EMPLOYEES"
  • bare name match (exact + CI)        "EMPLOYEES", "employees"
  • case-insensitive CONTAINS           partial / typo tolerance

All conditions run in one round-trip — no sequential fallback.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from graph.db.client import GraphClient  # type: ignore

logger = logging.getLogger(__name__)


class GraphService:
    """High-level graph query service.

    Parameters
    ----------
    client:
        The ``GraphClient`` singleton (may be ``None`` when graph is disabled).
    """

    def __init__(self, client: "GraphClient | None") -> None:
        self._client = client

    # ── Availability ───────────────────────────────────────────────────────────

    @property
    def available(self) -> bool:
        return self._client is not None and self._client.available

    # ── Low-level passthrough ──────────────────────────────────────────────────

    def run(self, cypher: str, params: dict | None = None) -> list[dict]:
        if not self.available:
            return []
        return self._client.run(cypher, params or {})  # type: ignore[union-attr]

    def run_write(self, cypher: str, params: dict | None = None) -> None:
        if not self.available:
            return
        self._client.run_write(cypher, params or {})  # type: ignore[union-attr]

    def run_write_raise(self, cypher: str, params: dict | None = None) -> None:
        if not self.available:
            raise RuntimeError("Graph database unavailable")
        self._client.run_write_raise(cypher, params or {})  # type: ignore[union-attr]

    # ── Utility ────────────────────────────────────────────────────────────────

    def clear(self) -> None:
        """Delete every node and relationship in the graph."""
        self.run_write_raise("MATCH (n) DETACH DELETE n")

    @staticmethod
    def extract_bare_name(qualified_name: str) -> str:
        """Return the local name from any qualified format.

        Examples
        --------
        "EMPLOYEES"            → "EMPLOYEES"
        "HR.EMPLOYEES"         → "EMPLOYEES"   (SQL dot notation)
        "Table:repo:EMPLOYEES" → "EMPLOYEES"   (colon-qualified)
        """
        return qualified_name.split(":")[-1].split(".")[-1].strip()

    # ── Neighbor lookup (used by the LLM tool) ─────────────────────────────────

    def get_neighbors(
        self,
        qualified_name: str,
        direction: str = "both",
        label: str = "",
    ) -> tuple[dict, list[dict], dict]:
        """Return all directly connected nodes for *qualified_name*.

        Returns ``(result_dict, text_docs, graph_payload)`` compatible with
        the ``execute_tool`` contract in ``tools.py``.

        The Cypher WHERE clause uses multiple OR conditions to tolerate:
          • full qualified names   "Table:repo:EMPLOYEES"
          • colon-suffix           any name ending in ":EMPLOYEES"
          • SQL dot notation       any name ending in ".EMPLOYEES" (case-insensitive)
          • bare name              exact or case-insensitive
          • substring              case-insensitive CONTAINS as last resort
        Everything runs in a **single** round-trip.
        """
        if not self.available:
            return {"error": "Graph database unavailable.", "edges": []}, [], {}

        # ── Direction pattern ──────────────────────────────────────────────────
        if direction == "outgoing":
            rel_pattern = "(a)-[r]->(b)"
        elif direction == "incoming":
            rel_pattern = "(a)<-[r]-(b)"
        else:
            rel_pattern = "(a)-[r]-(b)"

        # ── Optional label constraint on the anchor node ───────────────────────
        label_part = f":{label}" if label else ""
        pattern = rel_pattern.replace("(a)", f"(a{label_part})", 1)

        # ── Name normalization ─────────────────────────────────────────────────
        bare = self.extract_bare_name(qualified_name)
        bare_lower = bare.lower()

        logger.info(
            "GraphService.get_neighbors: qname=%r bare=%r direction=%r label=%r",
            qualified_name, bare, direction, label,
        )

        # ── Single comprehensive query ─────────────────────────────────────────
        rows = self._client.run(  # type: ignore[union-attr]
            f"""
            MATCH {pattern}
            WHERE a.qualified_name = $qname
               OR a.qualified_name ENDS WITH $colon_suffix
               OR toLower(a.qualified_name) ENDS WITH $dot_suffix_lower
               OR a.name = $bare
               OR toLower(a.name) = $bare_lower
               OR toLower(a.qualified_name) CONTAINS $bare_lower
            RETURN labels(a)[0]     AS from_label,
                   a.qualified_name  AS from_qname,
                   type(r)           AS rel,
                   labels(b)[0]     AS to_label,
                   b.qualified_name  AS to_qname,
                   b.name            AS to_name
            LIMIT 60
            """,
            {
                "qname":            qualified_name,
                "colon_suffix":     f":{bare}",
                "dot_suffix_lower": f".{bare_lower}",
                "bare":             bare,
                "bare_lower":       bare_lower,
            },
        )

        # ── Build edge list ────────────────────────────────────────────────────
        edges = [
            {
                "from": r["from_qname"],
                "rel":  r["rel"],
                "to":   r["to_qname"],
                "to_label": r["to_label"],
                "to_name":  r.get("to_name"),
            }
            for r in (rows or [])
        ]
        result = {"qualified_name": qualified_name, "edges": edges}

        if not edges:
            logger.info("GraphService.get_neighbors: no results for %r", qualified_name)
            return result, [], {}

        # ── Format as text for LLM context ────────────────────────────────────
        from_qname = rows[0]["from_qname"] if rows else qualified_name
        from_label = rows[0]["from_label"] if rows else ""
        lines = [f"Graph relationships for {from_qname} ({from_label}):"]
        for e in edges:
            name_hint = f" ({e['to_name']})" if e.get("to_name") else ""
            lines.append(f"  [{e['from']}] --{e['rel']}--> [{e['to']}]{name_hint}")
        text_doc = {
            "file":      f"graph:{from_qname}",
            "file_path": f"graph:{from_qname}",
            "text":      "\n".join(lines),
            "score":     1.0,
        }

        # ── Build vis-network payload for UI sidebar ───────────────────────────
        node_ids: set[str] = set()
        vis_nodes: list[dict] = []
        vis_edges: list[dict] = []
        for e in edges:
            for qn, lbl in [(e["from"], from_label), (e["to"], e["to_label"] or "")]:
                if qn and qn not in node_ids:
                    node_ids.add(qn)
                    short = qn.split(":")[-1] if ":" in qn else qn
                    vis_nodes.append({
                        "label": lbl,
                        "props": {"qualified_name": qn, "name": short},
                    })
            vis_edges.append({
                "from":       e["from"],
                "to":         e["to"],
                "rel":        e["rel"],
                "from_label": from_label,
                "to_label":   e.get("to_label", ""),
            })

        return result, [text_doc], {"nodes": vis_nodes, "edges": vis_edges}

    # ── Node search (used by search_graph_nodes tool) ─────────────────────────

    def search_nodes(
        self,
        query: str,
        label: str = "",
        limit: int = 20,
    ) -> tuple[dict, list[dict], dict]:
        """Search graph nodes by partial name (case-insensitive CONTAINS)."""
        if not self.available:
            return {"error": "Graph database unavailable.", "nodes": []}, [], {}
        label_part = f":{label}" if label else ""
        rows = self._client.run(  # type: ignore[union-attr]
            f"""
            MATCH (n{label_part})
            WHERE toLower(n.name) CONTAINS $q
               OR toLower(n.qualified_name) CONTAINS $q
            RETURN labels(n)[0]    AS label,
                   n.name          AS name,
                   n.qualified_name AS qualified_name,
                   n.repository    AS repository,
                   n.service       AS service
            LIMIT $limit
            """,
            {"q": query.lower(), "limit": limit},
        )
        if not rows:
            return (
                {"query": query, "nodes": [], "hint": f"No graph nodes match '{query}'."},
                [],
                {},
            )
        nodes = [
            {
                "label": r.get("label", ""),
                "name": r.get("name", ""),
                "qualified_name": r.get("qualified_name", ""),
                "repository": r.get("repository", ""),
                "service": r.get("service", ""),
            }
            for r in rows
        ]
        lines = [f"Graph nodes matching '{query}':"]
        for n in nodes:
            lines.append(f"  [{n['label']}] {n['name']}  \u2192  {n['qualified_name']}")
        text_doc = {
            "file": f"graph:search:{query}",
            "file_path": f"graph:search:{query}",
            "text": "\n".join(lines),
            "score": 1.0,
        }
        return {"query": query, "nodes": nodes}, [text_doc], {}

    # ── Table-centric queries (used by agent_routes.py) ────────────────────────

    def get_table_writers(self, table: str, repo: str = "") -> list[dict]:
        """Functions that WRITE to *table* (case-insensitive)."""
        table = table.upper()
        if repo:
            rows = self.run(
                """
                MATCH (f)-[r:WRITES_TO]->(t:Table)
                WHERE toUpper(t.name) = $table AND t.repository = $repo
                RETURN f, r, t
                """,
                {"table": table, "repo": repo},
            )
        else:
            rows = self.run(
                """
                MATCH (f)-[r:WRITES_TO]->(t:Table)
                WHERE toUpper(t.name) = $table
                RETURN f, r, t
                """,
                {"table": table},
            )
        return [
            {
                "function": dict(r["f"].items()),
                "rel":      dict(r["r"].items()),
                "table":    dict(r["t"].items()),
            }
            for r in rows
        ]

    def get_table_readers(self, table: str, repo: str = "") -> list[dict]:
        """Functions that READ from *table* (case-insensitive)."""
        table = table.upper()
        if repo:
            rows = self.run(
                """
                MATCH (f)-[r:READS_FROM]->(t:Table)
                WHERE toUpper(t.name) = $table AND t.repository = $repo
                RETURN f, r, t
                """,
                {"table": table, "repo": repo},
            )
        else:
            rows = self.run(
                """
                MATCH (f)-[r:READS_FROM]->(t:Table)
                WHERE toUpper(t.name) = $table
                RETURN f, r, t
                """,
                {"table": table},
            )
        return [
            {
                "function": dict(r["f"].items()),
                "rel":      dict(r["r"].items()),
                "table":    dict(r["t"].items()),
            }
            for r in rows
        ]

    def get_table_impact(self, table: str) -> dict:
        """Full blast radius for *table*: writers, readers, API endpoints, cron jobs."""
        table = table.upper()

        writers = self.run(
            """
            MATCH (f)-[r:WRITES_TO]->(t:Table)
            WHERE toUpper(t.name) = $table
            OPTIONAL MATCH (f)-[:BELONGS_TO*1..2]->(svc:Service)
            RETURN f.qualified_name AS fn_qname, f.name AS fn_name,
                   f.service AS service, f.source_file AS source_file,
                   r.operation AS operation, r.line AS line,
                   svc.name AS svc_name, t.name AS table_name
            """,
            {"table": table},
        )
        readers = self.run(
            """
            MATCH (f)-[r:READS_FROM]->(t:Table)
            WHERE toUpper(t.name) = $table
            OPTIONAL MATCH (f)-[:BELONGS_TO*1..2]->(svc:Service)
            RETURN f.qualified_name AS fn_qname, f.name AS fn_name,
                   f.service AS service, f.source_file AS source_file,
                   svc.name AS svc_name, t.name AS table_name
            """,
            {"table": table},
        )
        api_endpoints = self.run(
            """
            MATCH (t:Table)<-[:WRITES_TO|READS_FROM]-(f)
            WHERE toUpper(t.name) = $table
            MATCH (ep:ApiEndpoint)-[:HANDLED_BY|CALLS|BELONGS_TO*1..3]->(f)
            RETURN DISTINCT ep.qualified_name AS ep_qname, ep.method AS method, ep.path AS path,
                   ep.service AS service, f.name AS fn_name
            """,
            {"table": table},
        )
        cron_jobs = self.run(
            """
            MATCH (t:Table)<-[:WRITES_TO|READS_FROM]-(f)
            WHERE toUpper(t.name) = $table
            MATCH (cj:Job)-[:CALLS|EXECUTES|TRIGGERS]->(f)
            RETURN cj.qualified_name AS cj_qname, cj.name AS cj_name,
                   cj.schedule AS schedule, cj.framework AS framework,
                   f.name AS fn_name
            """,
            {"table": table},
        )
        return {
            "table":        table,
            "writers":      [dict(r) for r in writers],
            "readers":      [dict(r) for r in readers],
            "api_endpoints": [dict(r) for r in api_endpoints],
            "cron_jobs":    [dict(r) for r in cron_jobs],
        }

    def get_service_tables(self, service: str) -> list[dict]:
        """All tables a *service* reads or writes.

        Tries two strategies:
        1. Traverse from ApiService via CONTAINS edges (new graph model).
        2. Legacy fallback: functions with f.service matching the service name.
        """
        rows = self.run(
            """
            MATCH (svc)
            WHERE (svc:ApiService OR svc:Module OR svc:Service)
              AND (svc.name = $svc OR svc.service_id = $svc
                   OR toLower(svc.name) = toLower($svc))
            MATCH (svc)-[:CONTAINS*1..3]->(f)-[r:WRITES_TO|READS_FROM]->(t:Table)
            RETURN t.name AS table_name, t.repository AS repository,
                   type(r) AS rel_type, collect(DISTINCT f.name) AS functions
            ORDER BY t.name
            """,
            {"svc": service},
        )
        if not rows:
            # Legacy fallback: f.service property
            rows = self.run(
                """
                MATCH (f)-[r:WRITES_TO|READS_FROM]->(t:Table)
                WHERE f.service = $svc
                   OR f.qualified_name STARTS WITH ('Function:' + $svc)
                   OR f.qualified_name STARTS WITH ('Procedure:' + $svc)
                RETURN t.name AS table_name, t.repository AS repository,
                       type(r) AS rel_type, collect(DISTINCT f.name) AS functions
                ORDER BY t.name
                """,
                {"svc": service},
            )
        return [dict(r) for r in rows]

    def get_api_impact(self, method: str, path: str) -> list[dict]:
        """Tables touched by an API endpoint (method + path)."""
        rows = self.run(
            """
            MATCH (ep:ApiEndpoint)
            WHERE ep.method = $method AND ep.path = $path
            MATCH (ep)-[:HANDLED_BY]->(f)
            OPTIONAL MATCH (f)-[wr:WRITES_TO]->(t:Table)
            OPTIONAL MATCH (f)-[rr:READS_FROM]->(t2:Table)
            RETURN ep.qualified_name AS ep_qname, ep.service AS service,
                   f.qualified_name AS fn_qname, f.name AS fn_name,
                   collect(DISTINCT {table: t.name, op: 'WRITE'}) AS writes,
                   collect(DISTINCT {table: t2.name, op: 'READ'}) AS reads
            """,
            {"method": method, "path": path},
        )
        return [dict(r) for r in rows]

    def list_tables(self, repository: str = "") -> list[dict]:
        """All Table nodes, optionally filtered by *repository*."""
        if repository:
            rows = self.run(
                "MATCH (t:Table) WHERE t.repository = $repo RETURN t ORDER BY t.name",
                {"repo": repository},
            )
        else:
            rows = self.run("MATCH (t:Table) RETURN t ORDER BY t.name")
        return [dict(r["t"].items()) for r in rows]

    def list_node_labels(self) -> list[str]:
        """All distinct node labels in the graph."""
        rows = self.run("CALL db.labels() YIELD label RETURN label ORDER BY label")
        return [r["label"] for r in rows]

    # ── Scalable context methods (prevents LLM context overflow) ──────────────

    def get_node_context(
        self,
        qualified_name: str,
        limit: int = 10,
    ) -> tuple[dict, list[dict], dict]:
        """Return node properties + neighbor COUNTS grouped by (rel_type, label).

        Unlike get_neighbors() which returns all edges (can overflow context for
        large nodes), this returns aggregate counts so the LLM can decide which
        specific sub-query to issue next.

        Returns ``(result_dict, text_docs, graph_payload)`` compatible with
        the ``execute_tool`` contract in ``tools.py``.
        """
        if not self.available:
            return {"error": "Graph database unavailable."}, [], {}

        bare = self.extract_bare_name(qualified_name)
        bare_lower = bare.lower()

        # Find the node
        node_rows = self._client.run(  # type: ignore[union-attr]
            """
            MATCH (a)
            WHERE a.qualified_name = $qname
               OR a.qualified_name ENDS WITH $colon_suffix
               OR toLower(a.name) = $bare_lower
               OR toLower(a.qualified_name) CONTAINS $bare_lower
            RETURN a, labels(a)[0] AS label
            LIMIT 1
            """,
            {
                "qname": qualified_name,
                "colon_suffix": f":{bare}",
                "bare_lower": bare_lower,
            },
        )
        if not node_rows:
            return (
                {"qualified_name": qualified_name, "error": f"Node not found: {qualified_name}"},
                [],
                {},
            )

        node = dict(node_rows[0]["a"].items())
        node_label = node_rows[0]["label"]
        node_qname = node.get("qualified_name", qualified_name)

        # Outgoing neighbor counts
        out_rows = self._client.run(  # type: ignore[union-attr]
            """
            MATCH (a)
            WHERE a.qualified_name = $qname
            WITH a
            MATCH (a)-[r]->(b)
            RETURN type(r) AS rel_type, labels(b)[0] AS nb_label, count(*) AS cnt
            ORDER BY cnt DESC
            LIMIT $limit
            """,
            {"qname": node_qname, "limit": limit},
        )
        # Incoming neighbor counts
        in_rows = self._client.run(  # type: ignore[union-attr]
            """
            MATCH (a)
            WHERE a.qualified_name = $qname
            WITH a
            MATCH (b)-[r]->(a)
            RETURN type(r) AS rel_type, labels(b)[0] AS nb_label, count(*) AS cnt
            ORDER BY cnt DESC
            LIMIT $limit
            """,
            {"qname": node_qname, "limit": limit},
        )

        out_summary = [
            {"direction": "out", "rel_type": r["rel_type"], "label": r["nb_label"], "count": r["cnt"]}
            for r in (out_rows or [])
        ]
        in_summary = [
            {"direction": "in", "rel_type": r["rel_type"], "label": r["nb_label"], "count": r["cnt"]}
            for r in (in_rows or [])
        ]
        connections = out_summary + in_summary

        result = {
            "qualified_name": node_qname,
            "label": node_label,
            "properties": node,
            "connections_summary": connections,
        }

        lines = [f"Context for {node_qname} ({node_label}):"]
        for k, v in node.items():
            if v is not None and k != "qualified_name":
                lines.append(f"  {k}: {v}")
        lines.append("Connections (summary — use get_graph_neighbors to drill down):")
        for c in connections:
            arrow = "-->" if c["direction"] == "out" else "<--"
            lines.append(f"  [{c['count']}x] {arrow}{c['rel_type']}{arrow} :{c['label']}")

        text_doc = {
            "file": f"graph:context:{node_qname}",
            "file_path": f"graph:context:{node_qname}",
            "text": "\n".join(lines),
            "score": 1.0,
        }
        return result, [text_doc], {}

    def get_landscape_overview(self) -> tuple[dict, list[dict], dict]:
        """Return all landscape-tier nodes (ApiService, Database, FrontendApp, etc.) and their edges.

        Useful for high-level architecture questions.
        """
        if not self.available:
            return {"error": "Graph database unavailable."}, [], {}

        landscape_labels = [
            "ApiService", "Database", "FrontendApp", "JobPlatform",
            "Storage", "ExternalService",
        ]
        label_filter = " OR ".join(f"n:{lbl}" for lbl in landscape_labels)

        nodes_rows = self._client.run(  # type: ignore[union-attr]
            f"""
            MATCH (n)
            WHERE {label_filter}
            RETURN labels(n)[0] AS label, n.qualified_name AS qname, n.name AS name,
                   n.description AS description
            ORDER BY label, name
            """,
        )
        edges_rows = self._client.run(  # type: ignore[union-attr]
            f"""
            MATCH (a)-[r]->(b)
            WHERE ({label_filter.replace('n:', 'a:')})
              AND ({label_filter.replace('n:', 'b:')})
            RETURN a.qualified_name AS from_qname, type(r) AS rel, b.qualified_name AS to_qname
            """,
        )

        nodes = [{"label": r["label"], "qname": r["qname"], "name": r["name"]} for r in (nodes_rows or [])]
        edges = [{"from": r["from_qname"], "rel": r["rel"], "to": r["to_qname"]} for r in (edges_rows or [])]

        if not nodes:
            return {"nodes": [], "edges": []}, [], {}

        lines = ["Landscape overview — top-level architecture nodes:"]
        for n in nodes:
            lines.append(f"  [{n['label']}] {n['name']}  ({n['qname']})")
        if edges:
            lines.append("Landscape relationships:")
            for e in edges:
                lines.append(f"  {e['from']} --{e['rel']}--> {e['to']}")

        text_doc = {
            "file": "graph:landscape_overview",
            "file_path": "graph:landscape_overview",
            "text": "\n".join(lines),
            "score": 1.0,
        }
        return {"nodes": nodes, "edges": edges}, [text_doc], {}
