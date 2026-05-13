"""YAML job-definition extractor.

Reads per-job YAML files that describe batch jobs and their execution steps.

Expected file structure::

    name: PAYROLL_CALC_JOB
    job_platform_id: JP1          # overrides metadata.job_platform_id if present
    schedule: "0 1 1 * *"         # cron expression (optional)
    bounded_context: payroll
    description: Monthly payroll batch

    steps:
      - step: 1
        type: plsql               # calls a PL/SQL procedure
        package: PKG_PAYROLL
        procedure: OPEN_PAY_PERIOD
        repository: plsql_hr_repo # optional — defaults to metadata.plsql_repository
        description: Open pay period

      - step: 2
        type: sqlplus             # runs a .sql script via sqlplus
        script: sql/migrate.sql
        description: Run migration

      - step: 3
        type: csharp_exe          # invokes a compiled .exe
        executable: HrBatch.Transfer.exe
        args: "--mode transfer"
        description: Bank transfer

      - step: 4
        type: shell               # generic shell command
        command: python sync.py
        description: Sync metadata

Creates:
  - ``Job`` node per file.
  - ``(JobPlatform)-[:CONTAINS]->(Job)`` edge (cross-project, via landscape).
  - ``(Job)-[:CALLS {step}]->(Function)`` for ``type: plsql`` steps.
  - ``(Job)-[:EXECUTES {step, step_type}]->(File)`` for sqlplus / csharp_exe / shell steps.

``qualified_name`` patterns:
  - Job      : ``Job:{job_platform_id}:{JOB_NAME}``
  - Function : ``Function:{repository}:{PKG_NAME}.{PROC_NAME}``  (stub, merges with existing)
  - File     : ``File:{script_or_exe_path}``

``context.extra_tags`` consumed:
  - ``job_platform_id``    — fallback platform when not in YAML
  - ``plsql_repository``   — default repo key for resolving PL/SQL function QNames
"""
from __future__ import annotations

import logging
from pathlib import Path

import yaml

from graph.extractors.base import BaseExtractor
from graph.db.entities import ExtractionContext, ExtractionResult, GraphEdge, GraphNode
from graph.db import schema as S

logger = logging.getLogger(__name__)

_JOB_EXTENSIONS = {".yaml", ".yml"}

# step type → relationship type used for non-plsql targets
_STEP_TYPE_REL: dict[str, str] = {
    "sqlplus":    S.REL_EXECUTES,
    "csharp_exe": S.REL_EXECUTES,
    "shell":      S.REL_EXECUTES,
}


def _add_unique(result: ExtractionResult, node: GraphNode) -> None:
    """Add node only if its qualified_name is not already in result."""
    if not any(n.key_value == node.key_value for n in result.nodes):
        result.nodes.append(node)


class YamlJobsExtractor(BaseExtractor):
    """Extract Job nodes and execution edges from per-job YAML files."""

    def can_handle(self, file_path: str, text: str) -> bool:
        if Path(file_path).suffix.lower() not in _JOB_EXTENSIONS:
            return False
        # Must have steps: and at least one known step type marker
        if "steps:" not in text:
            return False
        return any(
            marker in text
            for marker in ("type: plsql", "type: sqlplus", "type: csharp_exe", "type: shell",
                           "job_platform_id:")
        )

    def extract(
        self, file_path: str, text: str, context: ExtractionContext
    ) -> ExtractionResult:
        result = ExtractionResult(
            source_file=file_path, extractor_name="YamlJobsExtractor"
        )

        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            logger.warning("YamlJobsExtractor: cannot parse %s — %s", file_path, exc)
            return result

        if not isinstance(data, dict):
            return result

        # ── Job identity ──────────────────────────────────────────────────────
        job_name = (data.get("name") or Path(file_path).stem).upper().replace(" ", "_")
        job_platform_id = (
            str(data.get("job_platform_id", ""))
            or context.extra_tags.get("job_platform_id", "")
        )
        schedule = data.get("schedule", "") or ""
        bounded_context = (
            data.get("bounded_context", "")
            or context.extra_tags.get("bounded_context", "")
        )
        description = data.get("description", "") or ""

        # Default PL/SQL repo for resolving Function QNames
        plsql_repo = (
            context.extra_tags.get("plsql_repository", "")
            or context.repository
        )

        # Job QName: Job:{job_platform_id}:{JOB_NAME}
        job_qname = (
            f"{S.LABEL_JOB}:{job_platform_id}:{job_name}"
            if job_platform_id
            else f"{S.LABEL_JOB}:{job_name}"
        )

        _add_unique(result, GraphNode(
            label=S.LABEL_JOB,
            key="qualified_name",
            key_value=job_qname,
            properties={
                "qualified_name": job_qname,
                "name": job_name,
                "job_platform_id": job_platform_id or None,
                "schedule": schedule or None,
                "bounded_context": bounded_context or None,
                "description": description or None,
                "source_file": file_path,
                "layer": "logic",
            },
        ))

        # ── (JobPlatform)-[:CONTAINS]->(Job) — cross-project edge ─────────────
        if job_platform_id:
            platform_qname = f"{S.LABEL_JOB_PLATFORM}:{job_platform_id}"
            result.edges.append(GraphEdge(
                from_label=S.LABEL_JOB_PLATFORM, from_key="qualified_name",
                from_key_value=platform_qname,
                to_label=S.LABEL_JOB, to_key="qualified_name",
                to_key_value=job_qname,
                rel_type=S.REL_CONTAINS,
                properties={"source_file": file_path},
            ))

        # ── Steps ─────────────────────────────────────────────────────────────
        for step_def in data.get("steps", []) or []:
            if not isinstance(step_def, dict):
                continue

            step_num = step_def.get("step", 0)
            step_type = (step_def.get("type") or "").lower().strip()
            step_desc = step_def.get("description", "") or ""

            if step_type == "plsql":
                # ── PL/SQL call ───────────────────────────────────────────────
                pkg = (step_def.get("package") or "").upper().strip()
                proc = (step_def.get("procedure") or "").upper().strip()
                if not pkg or not proc:
                    logger.warning(
                        "YamlJobsExtractor: [%s] step %s plsql missing package/procedure",
                        file_path, step_num,
                    )
                    continue

                repo = step_def.get("repository") or plsql_repo
                fn_qname = f"{S.LABEL_FUNCTION}:{repo}:{pkg}.{proc}"

                # Stub Function node — MERGEs with the real node if already loaded
                _add_unique(result, GraphNode(
                    label=S.LABEL_FUNCTION,
                    key="qualified_name",
                    key_value=fn_qname,
                    properties={
                        "qualified_name": fn_qname,
                        "name": f"{pkg}.{proc}",
                        "layer": "logic",
                    },
                ))
                result.edges.append(GraphEdge(
                    from_label=S.LABEL_JOB, from_key="qualified_name",
                    from_key_value=job_qname,
                    to_label=S.LABEL_FUNCTION, to_key="qualified_name",
                    to_key_value=fn_qname,
                    rel_type=S.REL_CALLS,
                    properties={
                        "step": step_num,
                        "step_type": "plsql",
                        "description": step_desc or None,
                        "source_file": file_path,
                    },
                ))

            elif step_type in _STEP_TYPE_REL:
                # ── External script / executable ──────────────────────────────
                target = (
                    step_def.get("script")
                    or step_def.get("executable")
                    or step_def.get("command")
                    or ""
                ).strip()
                if not target:
                    continue

                # Use only the first "word" of a shell command as the file reference
                target_ref = target.split()[0]
                file_qname = f"File:{target_ref}"

                _add_unique(result, GraphNode(
                    label=S.LABEL_FILE,
                    key="qualified_name",
                    key_value=file_qname,
                    properties={
                        "qualified_name": file_qname,
                        "name": Path(target_ref).name,
                        "path": target_ref,
                        "layer": "data",
                    },
                ))
                result.edges.append(GraphEdge(
                    from_label=S.LABEL_JOB, from_key="qualified_name",
                    from_key_value=job_qname,
                    to_label=S.LABEL_FILE, to_key="qualified_name",
                    to_key_value=file_qname,
                    rel_type=_STEP_TYPE_REL[step_type],
                    properties={
                        "step": step_num,
                        "step_type": step_type,
                        "args": step_def.get("args") or None,
                        "description": step_desc or None,
                        "source_file": file_path,
                    },
                ))

            else:
                logger.debug(
                    "YamlJobsExtractor: [%s] step %s unknown type %r — skipped",
                    file_path, step_num, step_type,
                )

        return result
