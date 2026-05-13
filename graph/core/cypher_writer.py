"""cypher_writer.py — Convert ExtractionResult to sharded .cypher files.

Replaces GraphWriter for the offline export pipeline.
Writes MERGE-based Cypher with project metadata attached to every node/edge.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from graph.db.entities import ExtractionResult, GraphEdge, GraphNode
from graph.core.manifest import FileEntry

logger = logging.getLogger(__name__)

_INDENT = "  "

# Phase → file prefix mapping
_PHASE_PREFIX = {
    "nodes":          "10_nodes",
    "edges_internal": "20_edges_internal",
    "edges_cross":    "30_edges_cross_project",
    "edges_global":   "40_edges_cross_repo",
    "metadata":       "90_metadata",
    "validate":       "99_validate",
}


class CypherWriter:
    """Write ExtractionResult to sharded .cypher files under project_dir.

    Parameters
    ----------
    project_dir : Path
        Destination directory for this project's .cypher files.
        Will be created if it doesn't exist.
    run_id : str
        Injected into every node/edge as `run_id` property.
    project_metadata : dict
        Extra properties from sources.yaml merged into every node.
    shard_size : int
        Max number of MERGE statements per file before creating a new shard.
    """

    def __init__(
        self,
        project_dir: Path,
        run_id: str,
        project_metadata: dict[str, Any],
        shard_size: int = 10_000,
    ) -> None:
        self._project_dir = project_dir
        self._run_id = run_id
        self._metadata = project_metadata
        self._shard_size = shard_size
        self._project_dir.mkdir(parents=True, exist_ok=True)

    def write(self, result: ExtractionResult) -> list[FileEntry]:
        """Write nodes and edges_internal; return FileEntry list for manifest."""
        entries: list[FileEntry] = []
        entries.extend(self._write_nodes(result.nodes))
        entries.extend(self._write_edges(result.edges, phase="edges_internal"))
        return entries

    def write_cross_edges(
        self,
        edges: list[GraphEdge],
        phase: str = "edges_cross",
    ) -> list[FileEntry]:
        """Write cross-project or cross-repo edges."""
        return self._write_edges(edges, phase=phase)

    def write_metadata(self, run_id: str, label_filter: str = "") -> list[FileEntry]:
        """Write a metadata update query for all nodes in this run."""
        lines = [
            f"// Metadata sweep — run_id={run_id}",
            "MATCH (n {run_id: $run_id})",
            "SET n.last_seen_at = datetime()",
            "RETURN count(n) AS updated;",
        ]
        path = self._project_dir / "90_metadata.cypher"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        rel_path = str(path.relative_to(self._project_dir.parent.parent.parent.parent))
        entry = FileEntry(
            path=rel_path,
            repo_key=self._metadata.get("repo_key", ""),
            project_key=self._metadata.get("project_key", ""),
            phase="metadata",
            part=1,
        )
        return [entry]

    # ── Internals ────────────────────────────────────────────────────────────

    def _write_nodes(self, nodes: list[GraphNode]) -> list[FileEntry]:
        return self._write_sharded(
            items=nodes,
            phase="nodes",
            render_fn=self._render_node,
        )

    def _write_edges(self, edges: list[GraphEdge], phase: str) -> list[FileEntry]:
        return self._write_sharded(
            items=edges,
            phase=phase,
            render_fn=self._render_edge,
        )

    def _write_sharded(
        self,
        items: list[Any],
        phase: str,
        render_fn,
    ) -> list[FileEntry]:
        if not items:
            return []

        prefix = _PHASE_PREFIX.get(phase, phase)
        entries: list[FileEntry] = []
        part = 1
        buf: list[str] = []
        counts = {"nodes": 0, "edges": 0}

        def flush() -> None:
            nonlocal part
            if not buf:
                return
            filename = f"{prefix}_{part:04d}.cypher"
            filepath = self._project_dir / filename
            header = _file_header(
                run_id=self._run_id,
                phase=phase,
                part=part,
                repo_key=self._metadata.get("repo_key", ""),
                project_key=self._metadata.get("project_key", ""),
            )
            filepath.write_text(header + "\n".join(buf) + "\n", encoding="utf-8")
            rel = _relative_path(filepath)
            entries.append(FileEntry(
                path=rel,
                repo_key=self._metadata.get("repo_key", ""),
                project_key=self._metadata.get("project_key", ""),
                phase=phase,
                part=part,
                node_count=counts.get("nodes", 0),
                edge_count=counts.get("edges", 0),
            ))
            logger.debug("Wrote %s (%d statements)", filepath.name, len(buf))
            buf.clear()
            counts["nodes"] = 0
            counts["edges"] = 0
            part += 1

        for item in items:
            stmt = render_fn(item)
            buf.append(stmt)
            if phase == "nodes":
                counts["nodes"] += 1
            else:
                counts["edges"] += 1
            if len(buf) >= self._shard_size:
                flush()

        flush()
        return entries

    def _render_node(self, node: GraphNode) -> str:
        props = dict(node.properties)
        props.update(self._node_extra_props(node))
        props_str = _props_to_cypher(props)
        # Properties that should be refreshed on every re-import
        match_props = {"run_id": self._run_id, "last_seen_at": "__datetime__"}
        if "layer" in props:
            match_props["layer"] = props["layer"]
        match_parts = []
        for k, v in match_props.items():
            if v == "__datetime__":
                match_parts.append(f"n.{k} = datetime()")
            else:
                match_parts.append(f"n.{k} = {_cypher_val(v)}")
        match_str = ", ".join(match_parts)
        return (
            f"MERGE (n:{node.label} {{{node.key}: {_cypher_val(node.key_value)}}})\n"
            f"ON CREATE SET {props_str}\n"
            f"ON MATCH SET {match_str};\n"
        )

    def _render_edge(self, edge: GraphEdge) -> str:
        props = dict(edge.properties)
        props["run_id"] = self._run_id
        props["source_type"] = props.get("source_type", "extracted")
        props_str = _props_to_cypher(props, prefix="r")
        return (
            f"MATCH (a:{edge.from_label} {{{edge.from_key}: {_cypher_val(edge.from_key_value)}}})\n"
            f"MATCH (b:{edge.to_label} {{{edge.to_key}: {_cypher_val(edge.to_key_value)}}})\n"
            f"MERGE (a)-[r:{edge.rel_type}]->(b)\n"
            f"ON CREATE SET {props_str};\n"
        )

    def _node_extra_props(self, node: GraphNode) -> dict[str, Any]:
        extras: dict[str, Any] = {
            "run_id": self._run_id,
            "source_type": node.source,
        }
        # Inject all metadata from sources.yaml automatically,
        # except internal pipeline keys and complex types (lists/dicts).
        _SKIP = {"repo_key", "project_key", "include_files", "confidence", "shard_size"}
        for key, val in self._metadata.items():
            if key in _SKIP:
                continue
            if isinstance(val, (list, dict)):
                continue
            extras.setdefault(key, val)
        return extras


# ── Helpers ───────────────────────────────────────────────────────────────────

def _cypher_val(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    escaped = str(v).replace("\\", "\\\\").replace("'", "\\'")
    return f"'{escaped}'"


def _props_to_cypher(props: dict[str, Any], prefix: str = "n") -> str:
    parts = []
    for k, v in props.items():
        if v is None:
            continue
        parts.append(f"{prefix}.{k} = {_cypher_val(v)}")
    return ", ".join(parts) if parts else f"{prefix}.updated = true"


def _file_header(run_id: str, phase: str, part: int, repo_key: str, project_key: str) -> str:
    return (
        f"// run_id: {run_id}\n"
        f"// repo: {repo_key}  project: {project_key}\n"
        f"// phase: {phase}  part: {part}\n"
        f"// Generated by graph/export.py — do not edit manually\n\n"
    )


def _relative_path(p: Path) -> str:
    """Return a slash-separated path relative to graph/exports/."""
    parts = p.parts
    try:
        idx = parts.index("exports")
        return "/".join(parts[idx:])
    except ValueError:
        return str(p)
