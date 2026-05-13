"""GraphWriter: idempotent MERGE of nodes and edges into Neo4j.

Uses MERGE so re-indexing a file is safe — no duplicates created.
"""

from __future__ import annotations

import logging
from typing import Any

from graph.db.client import GraphClient
from graph.db.entities import ExtractionResult, GraphEdge, GraphNode

logger = logging.getLogger(__name__)

# Maximum nodes/edges per batch write
_BATCH_SIZE = 100


class GraphWriter:
    def __init__(self, client: GraphClient) -> None:
        self._client = client

    def write(self, result: ExtractionResult) -> None:
        if not self._client.available or result.is_empty():
            return
        self._write_nodes(result.nodes)
        self._write_edges(result.edges)
        logger.debug(
            "GraphWriter: wrote %d nodes, %d edges from %s",
            len(result.nodes),
            len(result.edges),
            result.source_file,
        )

    def _write_nodes(self, nodes: list[GraphNode]) -> None:
        for i in range(0, len(nodes), _BATCH_SIZE):
            batch = nodes[i : i + _BATCH_SIZE]
            self._client.run_write(
                """
                UNWIND $rows AS row
                CALL apoc.merge.node([row.label], {qualified_name: row.qname}, row.props, row.props)
                YIELD node
                RETURN count(node)
                """,
                {"rows": [_node_to_row(n) for n in batch]},
            )

    def _write_edges(self, edges: list[GraphEdge]) -> None:
        for i in range(0, len(edges), _BATCH_SIZE):
            batch = edges[i : i + _BATCH_SIZE]
            self._client.run_write(
                """
                UNWIND $rows AS row
                MATCH (a {qualified_name: row.from_qname})
                MATCH (b {qualified_name: row.to_qname})
                CALL apoc.merge.relationship(a, row.rel_type, {}, row.props, b, row.props)
                YIELD rel
                RETURN count(rel)
                """,
                {"rows": [_edge_to_row(e) for e in batch]},
            )

    def delete_file_nodes(self, file_path: str) -> None:
        """Remove all nodes (and their edges) sourced from this file path.

        Called before re-indexing a file so stale graph data is cleaned up.
        """
        if not self._client.available:
            return
        self._client.run_write(
            """
            MATCH (n {source_file: $fp})
            DETACH DELETE n
            """,
            {"fp": file_path},
        )


# ── Helpers ───────────────────────────────────────────────────────────────────


def _node_to_row(node: GraphNode) -> dict[str, Any]:
    props = dict(node.properties)
    props.setdefault("source", node.source)
    # Trust the extractor's qualified_name — do NOT override name or qualified_name
    qname = props.get("qualified_name") or node.key_value
    return {
        "label": node.label,
        "qname": qname,
        "props": props,
    }


def _edge_to_row(edge: GraphEdge) -> dict[str, Any]:
    # from_key_value / to_key_value are already the full qualified_names from the extractor
    return {
        "from_qname": edge.from_key_value,
        "to_qname": edge.to_key_value,
        "rel_type": edge.rel_type,
        "props": edge.properties,
    }
