"""export.py — CLI entry point: run extractors and write .cypher files.

Usage:
    python graph/export.py [OPTIONS]

Options:
    --config PATH       Path to sources.yaml (default: graph/sources.yaml)
    --output DIR        Output root directory (default: graph/exports/)
    --repo REPO_KEY     Process only this repository
    --project KEY       Process only this project (requires --repo)
    --resume RUN_ID     Resume an incomplete run (e.g. RUN_20260513_230000)
    --dry-run           Run extractors but do not write any files
    --rules PATH        Path to graph_rules.yaml (default: graph_rules.yaml)
    -v, --verbose       Enable debug logging
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

# Ensure repo root is in sys.path when running as a script
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from graph.core import config_loader, manifest as manifest_mod
from graph.core.pipeline import ExportPipeline

logger = logging.getLogger(__name__)


def main() -> int:
    args = _parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%H:%M:%S",
    )

    config_path = Path(args.config)
    output_dir = Path(args.output)
    rules_path = args.rules

    logger.info("Loading config: %s", config_path)
    try:
        sources = config_loader.load(config_path)
    except (FileNotFoundError, ValueError) as exc:
        logger.error("Config error: %s", exc)
        return 1

    projects = config_loader.filter_projects(
        sources,
        repo_key=args.repo or None,
        project_key=args.project or None,
    )
    if not projects:
        logger.warning("No projects matched filter (--repo=%s --project=%s)", args.repo, args.project)
        return 0

    logger.info(
        "Found %d project(s) across %d repo(s)",
        len(projects),
        len({p.repo_key for p in projects}),
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    if args.resume:
        run_dir = output_dir / args.resume
        if not run_dir.exists():
            logger.error("Resume run directory not found: %s", run_dir)
            return 1
        mgr = manifest_mod.ManifestManager.resume(run_dir, str(config_path))
        logger.info("Resuming run: %s", mgr.run_id)
    else:
        mgr = manifest_mod.ManifestManager.new(output_dir, str(config_path))
        logger.info("Starting new run: %s", mgr.run_id)

    if args.dry_run:
        logger.info("[DRY RUN] No files will be written.")

    start = time.monotonic()
    total_nodes = total_edges = 0

    def on_progress(repo_key: str, project_key: str, n_nodes: int, n_edges: int) -> None:
        nonlocal total_nodes, total_edges
        total_nodes += n_nodes
        total_edges += n_edges
        print(f"  [{repo_key}/{project_key}]  nodes={n_nodes}  edges={n_edges}")

    pipeline = ExportPipeline(
        sources_config=sources,
        run_dir=mgr.run_dir,
        manifest=mgr,
        dry_run=args.dry_run,
        progress_callback=on_progress,
        rules_path=rules_path,
    )
    pipeline.run_landscapes()
    pipeline.run_project_nodes()
    pipeline.run(projects)

    elapsed = time.monotonic() - start
    print()
    print(f"Run ID : {mgr.run_id}")
    print(f"Output : {mgr.run_dir}")
    print(f"Total  : {total_nodes} nodes, {total_edges} edges in {elapsed:.1f}s")

    if not args.dry_run:
        print()
        print("Next steps:")
        print(f"  python graph/load.py --run {mgr.run_id}")
        print(f"  python graph/validate.py --run {mgr.run_id}")

    return 0


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Export graph nodes/edges as .cypher files from source code.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--config", default="graph/sources.yaml", help="Path to sources.yaml")
    p.add_argument("--output", default="graph/exports", help="Output root directory")
    p.add_argument("--repo", default="", help="Process only this repository key")
    p.add_argument("--project", default="", help="Process only this project key")
    p.add_argument("--resume", default="", metavar="RUN_ID", help="Resume incomplete run")
    p.add_argument("--dry-run", action="store_true", help="Extract but do not write files")
    p.add_argument("--rules", default="graph_rules.yaml", help="Path to graph_rules.yaml")
    p.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")
    return p.parse_args()


if __name__ == "__main__":
    sys.exit(main())
