"""CrossFileResolver: post-ingest Cypher pass to link stub Function nodes.

After all files in a repository are indexed, some CALLS edges point to
"stub" Function nodes (created with only name + repository because the
callee was unknown at parse time).  This pass finds real Function nodes
with a matching name in the same repository and re-targets the edges.

Usage:
    resolver = CrossFileResolver(graph_client)
    resolver.resolve(repository="my-repo")   # or omit for all repos
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class CrossFileResolver:
    """Runs post-ingest Cypher queries to fix cross-file CALLS/IMPORTS edges."""

    def __init__(self, client: Any) -> None:
        """client: ingest.graph.client.GraphClient"""
        self._client = client

    def resolve(self, repository: str = "") -> dict[str, int]:
        """Attempt to resolve stub nodes.  Returns counts of edges re-wired."""
        if not getattr(self._client, "available", False):
            return {"skipped": 1}

        results = {}
        try:
            results["calls_rewired"] = self._rewire_calls(repository)
        except Exception as exc:
            logger.warning("CrossFileResolver: CALLS rewire failed: %s", exc)

        try:
            results["imports_rewired"] = self._rewire_imports(repository)
        except Exception as exc:
            logger.warning("CrossFileResolver: IMPORTS rewire failed: %s", exc)

        logger.info("CrossFileResolver: resolved %s", results)
        return results

    def _rewire_calls(self, repository: str) -> int:
        """Find CALLS edges where callee is a stub (no file property) and
        there exists a real node with the same simple name in the same repo.
        Re-create the edge pointing to the real node.
        """
        repo_filter = "AND b.repository = $repo" if repository else ""
        query = f"""
        MATCH (a:Function)-[r:CALLS]->(b:Function)
        WHERE b.file IS NULL
          AND a.repository = b.repository
          {repo_filter}
        WITH a, b, r,
             split(b.name, '.')[-1] AS simple_name
        MATCH (real:Function)
        WHERE real.name ENDS WITH simple_name
          AND real.file IS NOT NULL
          AND real.repository = a.repository
        WITH a, b, r, real LIMIT 500
        CREATE (a)-[:CALLS]->(real)
        DELETE r
        RETURN count(real) AS cnt
        """
        params: dict = {}
        if repository:
            params["repo"] = repository
        rows = self._client.run_read(query, params)
        return int(rows[0]["cnt"]) if rows else 0

    def _rewire_imports(self, repository: str) -> int:
        """Similar pass for IMPORTS edges pointing to stub Module nodes."""
        repo_filter = "AND b.repository = $repo" if repository else ""
        query = f"""
        MATCH (a)-[r:IMPORTS]->(b:Module)
        WHERE b.source_path IS NULL
          AND a.repository = b.repository
          {repo_filter}
        WITH a, b, r
        MATCH (real:Module)
        WHERE real.name = b.name
          AND real.source_path IS NOT NULL
          AND real.repository = a.repository
        WITH a, b, r, real LIMIT 500
        CREATE (a)-[:IMPORTS]->(real)
        DELETE r
        RETURN count(real) AS cnt
        """
        params: dict = {}
        if repository:
            params["repo"] = repository
        rows = self._client.run_read(query, params)
        return int(rows[0]["cnt"]) if rows else 0
