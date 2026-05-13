"""config_loader.py — Load and validate sources.yaml, build ExtractionContext per project."""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from graph.db.entities import ExtractionContext, GraphEdge, GraphNode

logger = logging.getLogger(__name__)


def _expand_path(value: str) -> str:
    """Expand ${VAR} and ${VAR:-default} placeholders using environment variables."""
    def _replace(m: re.Match) -> str:
        var, sep, default = m.group(1).partition(":-")
        return os.environ.get(var, default if sep else m.group(0))
    return re.sub(r"\$\{([^}]+)\}", _replace, value)

# ── Required metadata fields per project type ─────────────────────────────────
_REQUIRED_FIELDS: dict[str, list[str]] = {
    "api":     ["service_id", "bounded_context", "base_path"],
    "plsql":   ["schema", "db_name"],
    "ddl":     ["db_name"],   # DDL-only folder: schema comes from the files themselves
    "angular": ["app_id"],
    "library": [],
    "files":   [],   # standalone file list — no required fields
    "jobs":    [],   # job manifest files — no required fields
}

@dataclass
class ProjectConfig:
    """Fully resolved config for one project after merging defaults."""
    repo_key: str
    project_key: str
    project_type: str
    abs_path: Path
    metadata: dict[str, Any]
    confidence: dict[str, float]
    shard_size: int
    context: ExtractionContext
    # For type='files': explicit file paths to process instead of scanning a folder.
    # Paths are absolute (resolved at load time relative to repo path).
    include_files: list[Path] = field(default_factory=list)


@dataclass
class SourcesConfig:
    version: int
    defaults: dict[str, Any]
    projects: list[ProjectConfig] = field(default_factory=list)
    landscape_nodes: list[GraphNode] = field(default_factory=list)
    landscape_edges: list[GraphEdge] = field(default_factory=list)


def load(config_path: str | Path) -> SourcesConfig:
    """Load sources.yaml and return fully resolved SourcesConfig."""
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"sources.yaml not found: {path}")

    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    global_defaults = raw.get("defaults", {})
    repos = raw.get("repositories", {})
    landscape_nodes, landscape_edges = _load_landscapes(raw.get("landscapes", {}))

    projects: list[ProjectConfig] = []

    # ── Parse repositories: section ───────────────────────────────────────────
    for repo_key, repo_cfg in repos.items():
        repo_defaults = _deep_merge(global_defaults, repo_cfg.get("defaults", {}))
        repo_path = Path(_expand_path(repo_cfg.get("path", "")))
        vcs_url = repo_cfg.get("vcs_url", "")
        source = repo_cfg.get("source", "git")

        for project_key, proj_cfg in repo_cfg.get("projects", {}).items():
            effective = _deep_merge(repo_defaults, proj_cfg.get("metadata", {}))
            project_type = proj_cfg.get("type", "library")

            _validate_required(repo_key, project_key, project_type, effective)

            rel_project_path = proj_cfg.get("path", project_key)
            abs_path = repo_path / rel_project_path

            confidence = dict(global_defaults.get("confidence", {}))
            confidence.update(repo_defaults.get("confidence", {}))
            confidence.update(proj_cfg.get("confidence", {}))

            shard_size = int(
                proj_cfg.get("shard_size")
                or repo_defaults.get("shard_size")
                or global_defaults.get("shard_size", 10000)
            )

            context = _build_context(
                repo_key=repo_key,
                project_key=project_key,
                project_type=project_type,
                repo_path=str(repo_path),
                abs_project_path=abs_path,
                vcs_url=vcs_url,
                source=source,
                effective=effective,
            )

            # For type='files', resolve each listed file relative to repo_path
            include_files: list[Path] = []
            if project_type == "files":
                for f in proj_cfg.get("include_files", []):
                    resolved = (repo_path / f).resolve()
                    include_files.append(resolved)

            projects.append(ProjectConfig(
                repo_key=repo_key,
                project_key=project_key,
                project_type=project_type,
                abs_path=abs_path,
                metadata=effective,
                confidence=confidence,
                shard_size=shard_size,
                context=context,
                include_files=include_files,
            ))
            logger.debug("Loaded project config: %s / %s (%s)", repo_key, project_key, project_type)

    # ── Parse files: section (top-level, no repo wrapper) ────────────────────
    # Each entry is a flat project config — repo_key = "files", project_key = entry key.
    # type defaults to "files"; include_files resolved relative to entry path.
    for file_key, file_cfg in raw.get("files", {}).items():
        project_type = file_cfg.get("type", "files")
        file_path = Path(_expand_path(file_cfg.get("path", ".")))
        effective = _deep_merge(global_defaults, file_cfg.get("metadata", {}))

        _validate_required("files", file_key, project_type, effective)

        confidence = dict(global_defaults.get("confidence", {}))
        confidence.update(file_cfg.get("confidence", {}))

        shard_size = int(
            file_cfg.get("shard_size")
            or global_defaults.get("shard_size", 10000)
        )

        context = _build_context(
            repo_key="files",
            project_key=file_key,
            project_type=project_type,
            repo_path=str(file_path),
            abs_project_path=file_path,
            vcs_url="",
            source="manual",
            effective=effective,
        )

        include_files: list[Path] = []
        if project_type == "files":
            for f in file_cfg.get("include_files", []):
                resolved = (file_path / f).resolve()
                include_files.append(resolved)

        projects.append(ProjectConfig(
            repo_key="files",
            project_key=file_key,
            project_type=project_type,
            abs_path=file_path,
            metadata=effective,
            confidence=confidence,
            shard_size=shard_size,
            context=context,
            include_files=include_files,
        ))
        logger.debug("Loaded file config: files / %s (%s)", file_key, project_type)

    return SourcesConfig(
        version=raw.get("version", 1),
        defaults=global_defaults,
        projects=projects,
        landscape_nodes=landscape_nodes,
        landscape_edges=landscape_edges,
    )


def filter_projects(
    config: SourcesConfig,
    repo_key: str | None = None,
    project_key: str | None = None,
) -> list[ProjectConfig]:
    """Filter projects by optional repo_key and/or project_key."""
    result = config.projects
    if repo_key:
        result = [p for p in result if p.repo_key == repo_key]
    if project_key:
        result = [p for p in result if p.project_key == project_key]
    return result


# ── Helpers ───────────────────────────────────────────────────────────────────

# Landscape: identity property key per label
_LANDSCAPE_ID_KEY: dict[str, str] = {
    "ApiService":      "service_id",
    "Database":        "db_name",
    "FrontendApp":     "app_id",
    "JobPlatform":     "job_platform_id",
    "Storage":         "storage_id",
    "ExternalService": "service_id",
}

# Landscape: YAML field → Neo4j edge type (source = ApiService/FrontendApp)
_LANDSCAPE_EDGE_FIELDS: dict[str, str] = {
    "uses_db":    "USES_DB",
    "calls_api":  "CALLS_API",
    "depends_on": "DEPENDS_ON",
}


def _load_landscapes(
    landscapes: dict[str, Any],
) -> tuple[list[GraphNode], list[GraphEdge]]:
    """Parse the `landscapes:` section of sources.yaml into GraphNode/GraphEdge lists."""
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []

    # First pass: build node_key → qualified_name index
    qname_index: dict[str, str] = {}
    for node_key, node_cfg in landscapes.items():
        label = node_cfg.get("label", "")
        name = node_cfg.get("name", node_key)
        id_key = _LANDSCAPE_ID_KEY.get(label, "name")
        props = node_cfg.get("properties", {})
        id_value = props.get(id_key) or name
        qname_index[node_key] = f"{label}:{id_value}"

    # Second pass: create GraphNode + GraphEdge objects
    for node_key, node_cfg in landscapes.items():
        label = node_cfg.get("label", "")
        if not label:
            logger.warning("Landscape node %r missing `label` — skipping", node_key)
            continue

        name = node_cfg.get("name", node_key)
        props = dict(node_cfg.get("properties", {}))
        id_key = _LANDSCAPE_ID_KEY.get(label, "name")
        id_value = props.get(id_key) or name
        qname = f"{label}:{id_value}"

        props.setdefault("name", name)
        props["layer"] = "landscape"
        props["qualified_name"] = qname

        nodes.append(GraphNode(
            label=label,
            key="qualified_name",
            key_value=qname,
            properties=props,
            source="manual",
        ))

        # Create edges from relationship fields (uses_db, calls_api, depends_on)
        for field_name, rel_type in _LANDSCAPE_EDGE_FIELDS.items():
            for target_key in node_cfg.get(field_name, []):
                target_qname = qname_index.get(target_key)
                if not target_qname:
                    logger.warning(
                        "Landscape edge [%s]-[%s]->[%s]: target not found — skipping",
                        node_key, rel_type, target_key,
                    )
                    continue
                target_label = target_qname.split(":")[0]
                edges.append(GraphEdge(
                    from_label=label,
                    from_key="qualified_name",
                    from_key_value=qname,
                    to_label=target_label,
                    to_key="qualified_name",
                    to_key_value=target_qname,
                    rel_type=rel_type,
                    properties={"source": "manual"},
                ))

    logger.info("Loaded %d landscape nodes, %d landscape edges", len(nodes), len(edges))
    return nodes, edges


def _validate_required(
    repo_key: str,
    project_key: str,
    project_type: str,
    metadata: dict[str, Any],
) -> None:
    required = _REQUIRED_FIELDS.get(project_type, [])
    missing = [f for f in required if not metadata.get(f)]
    if missing:
        raise ValueError(
            f"[{repo_key}/{project_key}] type={project_type!r} missing required "
            f"metadata fields: {missing}. Add them to sources.yaml."
        )


def _build_context(
    repo_key: str,
    project_key: str,
    project_type: str,
    repo_path: str,
    abs_project_path: Path,
    vcs_url: str,
    source: str,
    effective: dict[str, Any],
) -> ExtractionContext:
    return ExtractionContext(
        repository=repo_key,
        repository_path=repo_path,
        domain=effective.get("bounded_context", ""),
        service_name=effective.get("service_id", project_key),
        namespace_prefix=effective.get("namespace_prefix", ""),
        extra_tags={
            "project_key": project_key,
            "project_type": project_type,
            "system": effective.get("system", ""),
            "stack": effective.get("stack", ""),
            "owner_team": effective.get("owner_team", ""),
            **{k: v for k, v in effective.items()
               if k not in {"namespace_prefix", "service_id", "bounded_context",
                             "system", "stack", "owner_team"}},
        },
        source=source,
        vcs_url=vcs_url,
        repo_owner=effective.get("owner_team", ""),
        team_name=effective.get("owner_team", ""),
        db_name=effective.get("db_name", ""),
        workflow_name=effective.get("workflow_name", ""),
        workflow_id=effective.get("workflow_id", ""),
        scheduler_type=effective.get("scheduler_type", ""),
    )


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge override into base, returning a new dict."""
    result = dict(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result
