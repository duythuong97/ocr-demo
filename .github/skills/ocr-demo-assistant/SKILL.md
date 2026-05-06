---
name: ocr-demo-assistant
description: "Operate and troubleshoot the OCR demo stack (Flask + Solr + Qdrant + SQLite indexing). Use when handling indexing issues, semantic/fulltext search behavior, source management APIs, run-all jobs, and data reset workflows."
argument-hint: "Describe the task: index source, debug search result, fix semantic retrieval, or runbook check"
user-invocable: true
---

# OCR Demo Assistant

## When To Use
- You need to debug indexing jobs or run-all behavior.
- You need to verify API flows for source management and indexing control.
- You need to investigate semantic or hybrid search quality.
- You need to reset Solr/Qdrant/SQLite state safely.
- You need project-specific operational guidance before editing code.

## Inputs Expected
- Current issue or goal (for example: "run-all hangs", "semantic misses SQL query", "cannot add source").
- Environment context (local venv, Docker services status, changed files).
- Optional API payload or query samples.

## Procedure
1. Confirm environment and service health.
2. Identify active mode (fulltext, semantic, hybrid) and relevant endpoint.
3. Reproduce with smallest API request.
4. Inspect logs and indexing state.
5. Apply minimal code/config fix.
6. Re-run verification and summarize impact/risk.

Use the detailed command and endpoint checklist in [OCR demo runbook](./references/runbook.md).

## Project Guardrails
- Keep edits minimal and focused; do not refactor unrelated modules.
- Preserve APP_PREFIX behavior and subdirectory simulation logic.
- Prefer validating through existing endpoints before changing internals.
- For indexing changes, account for SQLite state recovery and duplicate-source prevention.
- For semantic retrieval, validate with SEMANTIC_MIN_SCORE-aware tests.

## Expected Output Format
- Findings: root cause and impacted flow.
- Changes: files touched and why.
- Validation: commands or API checks performed and outcomes.
- Follow-up: any manual steps still required.
