"""Workflow/CronJob extractor.

Detects scheduled job definitions in C# and TypeScript/JavaScript:
  - C#: Hangfire RecurringJob.AddOrUpdate, Quartz IJob, [CronJob] attribute
  - TypeScript: @Cron() decorator (NestJS), node-cron schedule(), setInterval patterns

Creates: Workflow nodes + TRIGGERS edges to Function nodes.
"""
from __future__ import annotations

import re
import logging
from pathlib import Path

from graph.extractors.base import BaseExtractor
from graph.db.entities import ExtractionContext, ExtractionResult, GraphEdge, GraphNode
from graph.db import schema as S

logger = logging.getLogger(__name__)

_CS_EXTENSIONS = {".cs"}
_TS_EXTENSIONS = {".ts", ".tsx", ".js", ".jsx"}

# ── C# patterns ───────────────────────────────────────────────────────────────
# Hangfire: RecurringJob.AddOrUpdate("job-id", () => service.Method(), "0 * * * *")
_HF_RECURRING = re.compile(
    r'RecurringJob\.AddOrUpdate\s*\(\s*["\']?(\w[\w\-]*)["\']?\s*,\s*[^,]+?(\w+)\s*\(\s*\)\s*,\s*["\']([^"\']+)["\']',
    re.IGNORECASE,
)
# Quartz: class MyJob : IJob
_QUARTZ_JOB = re.compile(r'class\s+(\w+)\s*:\s*(?:IJob|IJobWithData)', re.MULTILINE)
# [CronJob("0 * * * *")] attribute
_CRON_ATTR = re.compile(r'\[CronJob\s*\(\s*["\']([^"\']+)["\']', re.IGNORECASE)
# Class that has cron attr immediately before it
_CLASS_AFTER_ATTR = re.compile(r'\[CronJob[^\]]*\]\s*(?:\[.*?\]\s*)*(?:public|internal)?\s*class\s+(\w+)', re.DOTALL)

# ── TypeScript/NestJS patterns ────────────────────────────────────────────────
# @Cron('0 * * * * *') / @Cron(CronExpression.EVERY_HOUR)
_NEST_CRON = re.compile(r'@Cron\s*\(\s*[\'"`]([^\'"`]+)[\'"`]', re.IGNORECASE)
_NEST_CRON_METHOD = re.compile(r'@Cron[^\)]*\)\s+(?:async\s+)?(\w+)\s*\(', re.DOTALL)
# node-cron: cron.schedule('* * * * *', () => ...)
_NODE_CRON = re.compile(r'cron\.schedule\s*\(\s*[\'"`]([^\'"`]+)[\'"`]\s*,\s*(?:async\s+)?(?:\(\s*\)|function\s+(\w+))', re.IGNORECASE)

# Map framework name → normalized scheduler_type
_FRAMEWORK_TO_SCHEDULER: dict[str, str] = {
    "Hangfire":  "hangfire",
    "Quartz":    "quartz",
    "Attribute": "cron",
    "NestJS":    "cron",
    "node-cron": "cron",
}


class CronJobExtractor(BaseExtractor):
    def can_handle(self, file_path: str, text: str) -> bool:
        ext = Path(file_path).suffix.lower()
        if ext in _CS_EXTENSIONS:
            return bool(_HF_RECURRING.search(text) or _QUARTZ_JOB.search(text) or _CRON_ATTR.search(text))
        if ext in _TS_EXTENSIONS:
            return bool(_NEST_CRON.search(text) or _NODE_CRON.search(text))
        return False

    def extract(self, file_path: str, text: str, context: ExtractionContext) -> ExtractionResult:
        result = ExtractionResult(source_file=file_path, extractor_name="CronJobExtractor")
        ext = Path(file_path).suffix.lower()

        if ext in _CS_EXTENSIONS:
            _extract_csharp(text, file_path, context, result)
        elif ext in _TS_EXTENSIONS:
            _extract_typescript(text, file_path, context, result)
        return result


def _extract_csharp(text: str, file_path: str, context: ExtractionContext, result: ExtractionResult) -> None:
    repository = context.repository

    # Hangfire RecurringJob.AddOrUpdate
    for m in _HF_RECURRING.finditer(text):
        job_id, method_name, schedule = m.group(1), m.group(2), m.group(3)
        _add_cron_node(result, job_id, schedule, method_name, "Hangfire", repository, file_path, context)

    # Quartz IJob implementations
    for m in _QUARTZ_JOB.finditer(text):
        class_name = m.group(1)
        _add_cron_node(result, class_name, "", class_name + ".Execute", "Quartz", repository, file_path, context)

    # [CronJob] attribute classes
    for m in _CLASS_AFTER_ATTR.finditer(text):
        class_name = m.group(1)
        schedule_m = _CRON_ATTR.search(text, max(0, m.start() - 200), m.start())
        schedule = schedule_m.group(1) if schedule_m else ""
        _add_cron_node(result, class_name, schedule, class_name + ".Execute", "Attribute", repository, file_path, context)


def _extract_typescript(text: str, file_path: str, context: ExtractionContext, result: ExtractionResult) -> None:
    repository = context.repository

    # NestJS @Cron() with method
    for m in _NEST_CRON_METHOD.finditer(text):
        schedule_m = _NEST_CRON.search(text, max(0, m.start() - 100), m.start() + 10)
        schedule = schedule_m.group(1) if schedule_m else ""
        method_name = m.group(1)
        _add_cron_node(result, method_name, schedule, method_name, "NestJS", repository, file_path, context)

    # node-cron schedule()
    for m in _NODE_CRON.finditer(text):
        schedule = m.group(1)
        handler = m.group(2) or "anonymous"
        _add_cron_node(result, handler, schedule, handler, "node-cron", repository, file_path, context)


def _add_cron_node(
    result: ExtractionResult,
    job_name: str,
    schedule: str,
    handler_method: str,
    framework: str,
    repository: str,
    file_path: str,
    context: ExtractionContext,
) -> None:
    cron_qname = f"{S.LABEL_JOB}:{repository}:{job_name}"
    scheduler_type = (
        context.scheduler_type
        or _FRAMEWORK_TO_SCHEDULER.get(framework, framework.lower())
    )
    result.nodes.append(GraphNode(
        label=S.LABEL_JOB,
        key="qualified_name",
        key_value=cron_qname,
        properties={
            "qualified_name": cron_qname,
            "name": job_name,
            "schedule": schedule,
            "framework": framework,
            "scheduler_type": scheduler_type,
            "service": context.service_name or context.repository,
            "repository": repository,
            "source_file": file_path,
        },
    ))

    func_qname = f"{S.LABEL_FUNCTION}:{repository}:{handler_method}"
    result.nodes.append(GraphNode(
        label=S.LABEL_FUNCTION,
        key="qualified_name",
        key_value=func_qname,
        properties={
            "qualified_name": func_qname,
            "name": handler_method,
            "repository": repository,
            "source_file": file_path,
        },
    ))

    result.edges.append(GraphEdge(
        from_label=S.LABEL_JOB, from_key="qualified_name", from_key_value=cron_qname,
        to_label=S.LABEL_FUNCTION, to_key="qualified_name", to_key_value=func_qname,
        rel_type=S.REL_TRIGGERS,
        properties={"schedule": schedule, "framework": framework},
    ))
