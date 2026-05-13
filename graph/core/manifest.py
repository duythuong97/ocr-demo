"""manifest.py — Track run state: run_id, project status, file index, resume support."""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

ProjectStatus = Literal["pending", "running", "done", "failed"]


@dataclass
class FileEntry:
    path: str              # relative to run_dir
    repo_key: str
    project_key: str
    phase: str             # "nodes" | "edges_internal" | "edges_cross" | "edges_global" | "metadata" | "validate"
    part: int
    node_count: int = 0
    edge_count: int = 0


@dataclass
class ProjectEntry:
    repo_key: str
    project_key: str
    project_type: str
    status: ProjectStatus = "pending"
    error: str = ""
    node_count: int = 0
    edge_count: int = 0
    files: list[FileEntry] = field(default_factory=list)


@dataclass
class RunManifest:
    run_id: str
    generated_at: str
    config_path: str
    projects: list[ProjectEntry] = field(default_factory=list)


class ManifestManager:
    """Create, update and persist a run manifest.

    run_dir layout:
        <run_dir>/
            00_run_manifest.json
            repositories/<repo>/00_repo_manifest.json
    """

    def __init__(self, run_dir: Path, config_path: str) -> None:
        self._run_dir = run_dir
        self._manifest = RunManifest(
            run_id=run_dir.name,
            generated_at=_now_iso(),
            config_path=config_path,
        )
        self._run_dir.mkdir(parents=True, exist_ok=True)

    # ── Factory ───────────────────────────────────────────────────────────────

    @classmethod
    def new(cls, output_dir: Path, config_path: str) -> "ManifestManager":
        run_id = "RUN_" + datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
        run_dir = output_dir / run_id
        return cls(run_dir, config_path)

    @classmethod
    def resume(cls, run_dir: Path, config_path: str) -> "ManifestManager":
        """Load existing manifest from run_dir for resume."""
        mgr = cls(run_dir, config_path)
        manifest_path = run_dir / "00_run_manifest.json"
        if manifest_path.exists():
            with manifest_path.open("r", encoding="utf-8") as f:
                raw = json.load(f)
            mgr._manifest = _manifest_from_dict(raw)
            logger.info("Resuming run %s (%d projects)", mgr.run_id, len(mgr._manifest.projects))
        return mgr

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def run_id(self) -> str:
        return self._manifest.run_id

    @property
    def run_dir(self) -> Path:
        return self._run_dir

    # ── Project lifecycle ─────────────────────────────────────────────────────

    def register_project(self, repo_key: str, project_key: str, project_type: str) -> None:
        if not self._find_project(repo_key, project_key):
            self._manifest.projects.append(ProjectEntry(
                repo_key=repo_key,
                project_key=project_key,
                project_type=project_type,
            ))

    def is_done(self, repo_key: str, project_key: str) -> bool:
        entry = self._find_project(repo_key, project_key)
        return entry is not None and entry.status == "done"

    def set_running(self, repo_key: str, project_key: str) -> None:
        self._update_project(repo_key, project_key, status="running")
        self.save()

    def set_done(
        self,
        repo_key: str,
        project_key: str,
        node_count: int,
        edge_count: int,
        files: list[FileEntry],
    ) -> None:
        entry = self._find_project(repo_key, project_key)
        if entry:
            entry.status = "done"
            entry.node_count = node_count
            entry.edge_count = edge_count
            entry.files = files
        self.save()

    def set_failed(self, repo_key: str, project_key: str, error: str) -> None:
        entry = self._find_project(repo_key, project_key)
        if entry:
            entry.status = "failed"
            entry.error = error
        self.save()

    # ── File tracking ─────────────────────────────────────────────────────────

    def files_for_phase(self, phase: str) -> list[FileEntry]:
        """Return all registered files for a given phase across all projects."""
        result: list[FileEntry] = []
        for proj in self._manifest.projects:
            result.extend(f for f in proj.files if f.phase == phase)
        return result

    def all_cypher_files_ordered(self) -> list[Path]:
        """Return all .cypher files in load order: nodes → edges_internal → cross → global → metadata → validate."""
        order = ["nodes", "edges_internal", "edges_cross", "edges_global", "metadata", "validate"]
        result: list[Path] = []
        for phase in order:
            for entry in self.files_for_phase(phase):
                result.append(self._run_dir / entry.path)
        return result

    # ── Persist ───────────────────────────────────────────────────────────────

    def save(self) -> None:
        path = self._run_dir / "00_run_manifest.json"
        with path.open("w", encoding="utf-8") as f:
            json.dump(asdict(self._manifest), f, indent=2, ensure_ascii=False)

        # Per-repo manifests
        repos: dict[str, list[ProjectEntry]] = {}
        for proj in self._manifest.projects:
            repos.setdefault(proj.repo_key, []).append(proj)
        for repo_key, projs in repos.items():
            repo_dir = self._run_dir / "repositories" / repo_key
            repo_dir.mkdir(parents=True, exist_ok=True)
            repo_manifest_path = repo_dir / "00_repo_manifest.json"
            with repo_manifest_path.open("w", encoding="utf-8") as f:
                json.dump(
                    {
                        "run_id": self._manifest.run_id,
                        "repo_key": repo_key,
                        "generated_at": _now_iso(),
                        "projects": [asdict(p) for p in projs],
                    },
                    f, indent=2, ensure_ascii=False,
                )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _find_project(self, repo_key: str, project_key: str) -> ProjectEntry | None:
        for proj in self._manifest.projects:
            if proj.repo_key == repo_key and proj.project_key == project_key:
                return proj
        return None

    def _update_project(self, repo_key: str, project_key: str, **kwargs: Any) -> None:
        entry = self._find_project(repo_key, project_key)
        if entry:
            for k, v in kwargs.items():
                setattr(entry, k, v)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _manifest_from_dict(raw: dict) -> RunManifest:
    projects = []
    for p in raw.get("projects", []):
        files = [FileEntry(**fe) for fe in p.pop("files", [])]
        projects.append(ProjectEntry(**p, files=files))
    return RunManifest(
        run_id=raw["run_id"],
        generated_at=raw["generated_at"],
        config_path=raw.get("config_path", ""),
        projects=projects,
    )
