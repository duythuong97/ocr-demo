"""pipeline.py — Orchestrate: config → scan files → extract → write .cypher files.

Reuses ingest/extractors/* and ingest/pipeline/registry.py without modification.
Does NOT write to Neo4j.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from graph.db.entities import ExtractionResult, GraphEdge, GraphNode
from graph.db import schema as S
from graph.pipeline.registry import ExtractorRegistry

from graph.core.config_loader import ProjectConfig, SourcesConfig
from graph.core.cypher_writer import CypherWriter
from graph.core.manifest import FileEntry, ManifestManager

logger = logging.getLogger(__name__)

# File extensions worth scanning (extractor's can_handle() does the real filtering)
_SCAN_EXTENSIONS = {
    ".cs", ".csproj", ".config", ".json", ".xml",
    ".ts", ".tsx", ".js",
    ".sql", ".ddl", ".fnc", ".pks", ".pkb", ".prc", ".trg",
    ".yaml", ".yml", ".resx",
    ".csv",
}


class ExportPipeline:
    """Run extractors on source files and write results as .cypher files.

    Parameters
    ----------
    sources_config : SourcesConfig
        Fully resolved config from config_loader.
    run_dir : Path
        Root directory for this run's output (inside graph/exports/).
    manifest : ManifestManager
        Tracks project status + file index.
    dry_run : bool
        If True, run extractors but do not write any files.
    progress_callback : callable, optional
        Called with (repo_key, project_key, n_nodes, n_edges) after each project.
    rules_path : str
        Path to graph_rules.yaml for rule-based extractors.
    """

    def __init__(
        self,
        sources_config: SourcesConfig,
        run_dir: Path,
        manifest: ManifestManager,
        dry_run: bool = False,
        progress_callback: Callable[[str, str, int, int], None] | None = None,
        rules_path: str = "",
    ) -> None:
        self._config = sources_config
        self._run_dir = run_dir
        self._manifest = manifest
        self._dry_run = dry_run
        self._progress_cb = progress_callback
        self._rules_path = rules_path

        # Shared registry across all projects (extractors are stateless)
        self._registry = ExtractorRegistry()
        self._registry.load_defaults()
        if rules_path:
            self._registry.load_rules(rules_path)

    def run(self, projects: list[ProjectConfig]) -> None:
        """Process all given projects in order, then flush cross-project edges."""
        # cross-project edge buffer: repo_key → list[GraphEdge]
        cross_edges: dict[str, list[GraphEdge]] = {}

        for proj in projects:
            self._manifest.register_project(proj.repo_key, proj.project_key, proj.project_type)

            if self._manifest.is_done(proj.repo_key, proj.project_key):
                logger.info("Skipping (already done): %s / %s", proj.repo_key, proj.project_key)
                continue

            self._manifest.set_running(proj.repo_key, proj.project_key)
            try:
                collected, x_edges = self._process_project(proj)
                cross_edges.setdefault(proj.repo_key, []).extend(x_edges)
            except Exception as exc:
                logger.error(
                    "Failed: %s / %s — %s", proj.repo_key, proj.project_key, exc, exc_info=True
                )
                self._manifest.set_failed(proj.repo_key, proj.project_key, str(exc))

        # Flush cross-project edges per repo
        self._flush_cross_edges(cross_edges)

    # ── Per-project ───────────────────────────────────────────────────────────

    def _process_project(self, proj: ProjectConfig) -> tuple[ExtractionResult, list[GraphEdge]]:
        # Determine which files to process
        if proj.project_type == "files":
            files_to_process = [f for f in proj.include_files if f.exists()]
            missing = [f for f in proj.include_files if not f.exists()]
            for f in missing:
                logger.warning("[%s/%s] include_files: file not found: %s — skipping", proj.repo_key, proj.project_key, f)
        else:
            if not proj.abs_path.exists():
                logger.warning(
                    "[%s/%s] path does not exist: %s — skipping",
                    proj.repo_key, proj.project_key, proj.abs_path,
                )
                self._manifest.set_done(proj.repo_key, proj.project_key, 0, 0, [])
                return ExtractionResult(), []
            files_to_process = list(_scan_files(proj.abs_path))

        combined = ExtractionResult()
        for file_path in files_to_process:
            text = _read_file(file_path)
            if text is None:
                continue
            extractors = self._registry.extractors_for(str(file_path), text)
            for extractor in extractors:
                try:
                    result = extractor.extract(str(file_path), text, proj.context)
                    result.source_file = str(file_path)
                    combined = combined.merge(result)
                except Exception as exc:
                    logger.warning(
                        "Extractor %s failed on %s: %s",
                        type(extractor).__name__, file_path.name, exc,
                    )

        # Separate cross-project edges (edges whose targets are outside this project's path)
        internal_edges, cross_edges = _split_edges(combined.edges, proj)

        files: list[FileEntry] = []
        if not self._dry_run and not combined.is_empty():
            project_dir = _project_dir(self._run_dir, proj)
            writer = CypherWriter(
                project_dir=project_dir,
                run_id=self._manifest.run_id,
                project_metadata={
                    **proj.metadata,
                    "repo_key": proj.repo_key,
                    "project_key": proj.project_key,
                },
                shard_size=proj.shard_size,
            )
            internal_result = ExtractionResult(
                nodes=combined.nodes,
                edges=internal_edges,
                source_file=combined.source_file,
            )
            files = writer.write(internal_result)

        n_nodes = len(combined.nodes)
        n_edges = len(combined.edges)
        self._manifest.set_done(proj.repo_key, proj.project_key, n_nodes, n_edges, files)

        if self._progress_cb:
            self._progress_cb(proj.repo_key, proj.project_key, n_nodes, n_edges)
        else:
            logger.info(
                "[%s/%s] %d nodes, %d edges (%d cross)",
                proj.repo_key, proj.project_key, n_nodes, n_edges, len(cross_edges),
            )

        return combined, cross_edges

    # ── Project/Module layer (Layer 2) ──────────────────────────────────────

    def run_project_nodes(self) -> None:
        """Synthesise Project nodes (Layer 2) from sources.yaml project entries.

        For every project definition in repositories:, create:
          - A Project node  (qualified_name = Project:{repo_key}:{project_key})
          - A CONTAINS edge from the owning Landscape node when linkage metadata exists.
        """
        # Build a lookup: (label, id_key, id_value) → landscape QName
        landscape_index: dict[tuple[str, str], str] = {}
        for node in self._config.landscape_nodes:
            for prop_key in ("service_id", "db_name", "app_id", "job_platform_id", "storage_id"):
                val = node.properties.get(prop_key)
                if val:
                    landscape_index[(prop_key, str(val))] = node.key_value  # qname

        nodes: list[GraphNode] = []
        edges: list[GraphEdge] = []

        for proj in self._config.projects:
            qname = f"Project:{proj.repo_key}:{proj.project_key}"
            props: dict = {
                "qualified_name": qname,
                "name": proj.project_key,
                "project_type": proj.project_type,
                "repo_key": proj.repo_key,
                "layer": "project",
                "source": "config",
            }
            # Carry useful metadata properties onto the node
            for key in ("service_id", "db_name", "app_id", "job_platform_id",
                        "bounded_context", "stack", "base_path", "schema"):
                val = proj.metadata.get(key)
                if val:
                    props[key] = val

            nodes.append(GraphNode(
                label=S.LABEL_PROJECT,
                key="qualified_name",
                key_value=qname,
                properties=props,
                source="manual",
            ))

            # Find parent Landscape node via metadata linkage
            parent_qname: str | None = None
            for id_key in ("service_id", "app_id", "job_platform_id", "storage_id"):
                val = proj.metadata.get(id_key)
                if val:
                    parent_qname = landscape_index.get((id_key, str(val)))
                    if parent_qname:
                        break
            # db_name: link Project to Database only when there is no service_id
            # (PL/SQL projects belong directly to the Database, not via a service)
            if not parent_qname:
                db = proj.metadata.get("db_name")
                if db:
                    parent_qname = landscape_index.get(("db_name", str(db)))

            if parent_qname:
                parent_label = parent_qname.split(":")[0]
                edges.append(GraphEdge(
                    from_label=parent_label,
                    from_key="qualified_name",
                    from_key_value=parent_qname,
                    to_label=S.LABEL_PROJECT,
                    to_key="qualified_name",
                    to_key_value=qname,
                    rel_type=S.REL_CONTAINS,
                    properties={"source": "config"},
                ))
            else:
                logger.debug(
                    "Project %s/%s has no landscape parent (no service_id/db_name/app_id/job_platform_id in metadata)",
                    proj.repo_key, proj.project_key,
                )

        self._manifest.register_project("_projects", "_projects", "synthesized")

        if not nodes:
            self._manifest.set_done("_projects", "_projects", 0, 0, [])
            return

        if self._dry_run:
            logger.info("[DRY RUN] Projects: %d nodes, %d edges", len(nodes), len(edges))
            self._manifest.set_done("_projects", "_projects", len(nodes), len(edges), [])
            return

        project_dir = self._run_dir / "repositories" / "_projects" / "_projects"
        writer = CypherWriter(
            project_dir=project_dir,
            run_id=self._manifest.run_id,
            project_metadata={"repo_key": "_projects", "project_key": "_projects"},
            shard_size=10_000,
        )
        result = ExtractionResult(
            nodes=nodes,
            edges=edges,
            source_file="sources.yaml",
            extractor_name="project_synthesizer",
        )
        files = writer.write(result)
        self._manifest.set_done("_projects", "_projects", len(nodes), len(edges), files)
        logger.info("Projects: %d nodes, %d edges → %d file(s)", len(nodes), len(edges), len(files))

    # ── Landscape layer (Tầng Landscape) ─────────────────────────────────────

    def run_landscapes(self) -> None:
        """Write Landscape nodes + edges as a pseudo-project before repository extraction."""
        nodes = self._config.landscape_nodes
        edges = self._config.landscape_edges

        self._manifest.register_project("landscape", "landscape", "landscape")

        if not nodes and not edges:
            logger.info("No landscape nodes defined in sources.yaml — skipping")
            self._manifest.set_done("landscape", "landscape", 0, 0, [])
            return

        if self._dry_run:
            logger.info("[DRY RUN] Landscape: %d nodes, %d edges", len(nodes), len(edges))
            self._manifest.set_done("landscape", "landscape", len(nodes), len(edges), [])
            return

        project_dir = self._run_dir / "repositories" / "landscape" / "landscape"
        writer = CypherWriter(
            project_dir=project_dir,
            run_id=self._manifest.run_id,
            project_metadata={"repo_key": "landscape", "project_key": "landscape"},
            shard_size=10_000,
        )
        result = ExtractionResult(
            nodes=nodes,
            edges=edges,
            source_file="sources.yaml",
            extractor_name="landscape_loader",
        )
        files = writer.write(result)
        self._manifest.set_done("landscape", "landscape", len(nodes), len(edges), files)
        logger.info(
            "Landscape: %d nodes, %d edges → %d file(s)",
            len(nodes), len(edges), len(files),
        )

    # ── Cross-project / cross-repo ────────────────────────────────────────────

    def _flush_cross_edges(self, cross_edges: dict[str, list[GraphEdge]]) -> None:
        for repo_key, edges in cross_edges.items():
            if not edges or self._dry_run:
                continue
            repo_scope_dir = self._run_dir / "repositories" / repo_key / "repo_scope"
            # Use a minimal metadata dict for cross-project edges
            writer = CypherWriter(
                project_dir=repo_scope_dir,
                run_id=self._manifest.run_id,
                project_metadata={"repo_key": repo_key, "project_key": "_cross"},
                shard_size=10_000,
            )
            entries = writer.write_cross_edges(edges, phase="edges_cross")
            logger.info("[%s] wrote %d cross-project edges → %d file(s)", repo_key, len(edges), len(entries))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _scan_files(root: Path):
    """Yield all files with known extensions under root."""
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in _SCAN_EXTENSIONS:
            yield path


def _read_file(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        logger.debug("Cannot read %s: %s", path, exc)
        return None


def _project_dir(run_dir: Path, proj: ProjectConfig) -> Path:
    return run_dir / "repositories" / proj.repo_key / "projects" / proj.project_key


def _split_edges(
    edges: list[GraphEdge],
    proj: ProjectConfig,
) -> tuple[list[GraphEdge], list[GraphEdge]]:
    """Heuristic split: edges whose target qualified_name contains another service_id
    are treated as cross-project. All others are internal."""
    service_id = proj.metadata.get("service_id", proj.project_key)
    internal, cross = [], []
    for edge in edges:
        target_val = edge.to_key_value or ""
        if service_id and service_id.lower() in target_val.lower():
            internal.append(edge)
        elif _looks_external(target_val, proj):
            cross.append(edge)
        else:
            internal.append(edge)
    return internal, cross


def _looks_external(value: str, proj: ProjectConfig) -> bool:
    """Return True if the value references a known identifier outside this project."""
    repo_key = proj.repo_key.lower()
    project_key = proj.project_key.lower()
    v = value.lower()
    # If value explicitly starts with a different repo or project key, it's cross
    if v.startswith(repo_key) or v.startswith(project_key):
        return False
    # Values that look like fully qualified external names (contain colon separator)
    if ":" in v and not v.startswith(repo_key):
        return True
    return False
