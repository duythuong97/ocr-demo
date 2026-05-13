"""load.py — CLI entry point: load exported .cypher files into Neo4j.

Usage:
    python graph/load.py --run RUN_20260513_230000 [OPTIONS]

Options:
    --run RUN_ID        Run ID to load (required)
    --output DIR        Output root directory (default: graph/exports/)
    --repo REPO_KEY     Load only this repository
    --phase PHASE       Load only this phase: nodes|edges_internal|edges_cross|edges_global|all
    --batch-size N      Statements per transaction batch (default: 1000)
    --dry-run           Parse files but do not execute against Neo4j
    -v, --verbose       Enable debug logging
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from pathlib import Path
from typing import Iterator

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

logger = logging.getLogger(__name__)

# Load order: phase name → pass number
_PHASE_ORDER = [
    "nodes",
    "edges_internal",
    "edges_cross",
    "edges_global",
    "metadata",
]

# Statement terminator
_STMT_END = re.compile(r";\s*$", re.MULTILINE)


def main() -> int:
    args = _parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%H:%M:%S",
    )

    output_dir = Path(args.output)
    run_dir = output_dir / args.run
    if not run_dir.exists():
        logger.error("Run directory not found: %s", run_dir)
        return 1

    # Load manifest
    manifest_path = run_dir / "00_run_manifest.json"
    if not manifest_path.exists():
        logger.error("Manifest not found: %s", manifest_path)
        return 1
    with manifest_path.open("r", encoding="utf-8") as f:
        manifest = json.load(f)

    # Collect .cypher files in load order
    files_by_phase: dict[str, list[Path]] = {phase: [] for phase in _PHASE_ORDER}
    for proj in manifest.get("projects", []):
        if args.repo and proj["repo_key"] != args.repo:
            continue
        for fe in proj.get("files", []):
            phase = fe["phase"]
            if phase in files_by_phase:
                p = output_dir / fe["path"].replace("exports/", "", 1)
                if p.exists():
                    files_by_phase[phase].append(p)

    # Also pick up cross/global files from repo_scope and global dirs
    _collect_loose_files(run_dir, files_by_phase, args.repo)

    phases_to_run = [args.phase] if args.phase != "all" else _PHASE_ORDER
    total_stmts = total_errors = 0

    if not args.dry_run:
        try:
            from neo4j import GraphDatabase  # type: ignore
            import config as cfg
            driver = GraphDatabase.driver(cfg.NEO4J_URL, auth=(cfg.NEO4J_USER, cfg.NEO4J_PASSWORD))
        except ImportError:
            logger.error("neo4j driver not installed. Run: pip install neo4j")
            return 1
    else:
        driver = None
        logger.info("[DRY RUN] No statements will be executed.")

    start = time.monotonic()

    for phase in phases_to_run:
        files = files_by_phase.get(phase, [])
        if not files:
            continue
        logger.info("=== Phase: %s (%d file(s)) ===", phase, len(files))
        for cypher_file in sorted(files):
            n, err = _load_file(cypher_file, driver, args.batch_size, args.dry_run)
            total_stmts += n
            total_errors += err
            status = "OK" if err == 0 else f"ERRORS={err}"
            logger.info("  %s  [%d stmts] %s", cypher_file.name, n, status)

    elapsed = time.monotonic() - start
    print()
    print(f"Run ID   : {args.run}")
    print(f"Stmts    : {total_stmts}")
    print(f"Errors   : {total_errors}")
    print(f"Elapsed  : {elapsed:.1f}s")

    if driver:
        driver.close()

    return 0 if total_errors == 0 else 1


def _load_file(path: Path, driver, batch_size: int, dry_run: bool) -> tuple[int, int]:
    """Parse + execute one .cypher file. Returns (stmt_count, error_count)."""
    text = path.read_text(encoding="utf-8")
    statements = _split_statements(text)
    if not statements:
        return 0, 0

    errors = 0
    if dry_run:
        return len(statements), 0

    # Execute in batches inside transactions
    for batch in _chunks(statements, batch_size):
        try:
            with driver.session() as session:
                for stmt in batch:
                    session.run(stmt)
        except Exception as exc:
            logger.warning("Batch error in %s: %s", path.name, exc)
            errors += 1

    return len(statements), errors


def _split_statements(text: str) -> list[str]:
    """Split Cypher text on `;` ignoring comments and blank lines."""
    # Strip line comments
    lines = [l for l in text.splitlines() if not l.strip().startswith("//")]
    clean = "\n".join(lines)
    parts = [s.strip() for s in clean.split(";")]
    return [s for s in parts if s]


def _chunks(lst: list, n: int) -> Iterator[list]:
    for i in range(0, len(lst), n):
        yield lst[i : i + n]


def _collect_loose_files(
    run_dir: Path,
    files_by_phase: dict[str, list[Path]],
    repo_filter: str,
) -> None:
    """Collect cross/global .cypher files not tracked in manifest (repo_scope + global dirs)."""
    # repo_scope cross-project edges
    for repo_scope_dir in (run_dir / "repositories").glob("*/repo_scope"):
        repo_key = repo_scope_dir.parent.name
        if repo_filter and repo_key != repo_filter:
            continue
        for f in sorted(repo_scope_dir.glob("30_edges_cross_project_*.cypher")):
            if f not in files_by_phase["edges_cross"]:
                files_by_phase["edges_cross"].append(f)

    # global cross-repo edges
    global_dir = run_dir / "global"
    if global_dir.exists():
        for f in sorted(global_dir.glob("40_edges_cross_repo_*.cypher")):
            if f not in files_by_phase["edges_global"]:
                files_by_phase["edges_global"].append(f)
        for f in sorted(global_dir.glob("90_metadata*.cypher")):
            if f not in files_by_phase["metadata"]:
                files_by_phase["metadata"].append(f)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Load exported .cypher files into Neo4j.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--run", required=True, metavar="RUN_ID", help="Run ID to load")
    p.add_argument("--output", default="graph/exports", help="Output root directory")
    p.add_argument("--repo", default="", help="Load only this repository key")
    p.add_argument(
        "--phase",
        default="all",
        choices=_PHASE_ORDER + ["all"],
        help="Load only this phase (default: all)",
    )
    p.add_argument("--batch-size", type=int, default=1000, dest="batch_size")
    p.add_argument("--dry-run", action="store_true", help="Parse but do not execute")
    p.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")
    return p.parse_args()


if __name__ == "__main__":
    sys.exit(main())
