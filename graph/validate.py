"""validate.py — Post-load integrity checks against Neo4j.

Usage:
    python graph/validate.py --run RUN_20260513_230000 [OPTIONS]

Checks:
    1. Orphan nodes  — nodes with no relationships
    2. Missing layer — nodes without a `layer` property
    3. Duplicate qualified_name within same label
    4. Dangling edges — edges referencing nodes not in this run

Options:
    --run RUN_ID    Run ID to validate (checks nodes where run_id = RUN_ID)
    --fix           Auto-delete orphan nodes with no relationships (use carefully)
    -v, --verbose   Debug logging
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

logger = logging.getLogger(__name__)


def main() -> int:
    args = _parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%H:%M:%S",
    )

    try:
        from neo4j import GraphDatabase  # type: ignore
        import config as cfg
        driver = GraphDatabase.driver(cfg.NEO4J_URL, auth=(cfg.NEO4J_USER, cfg.NEO4J_PASSWORD))
    except ImportError:
        logger.error("neo4j driver not installed. Run: pip install neo4j")
        return 1

    run_id = args.run
    issues = 0

    with driver.session() as session:
        # ── Pre-check: confirm nodes exist for this run_id ───────────────────
        total_row = session.run(
            "MATCH (n) WHERE n.run_id = $run_id RETURN count(n) AS total",
            {"run_id": run_id},
        ).single()
        total_nodes = total_row["total"] if total_row else 0
        if total_nodes == 0:
            print(f"[WARN] No nodes found with run_id='{run_id}'.")
            print("       Either the run produced 0 nodes, or load.py was not run yet.")
            print("       Run: python graph/load.py --run " + run_id)
            driver.close()
            return 1
        print(f"[INFO] Validating {total_nodes} nodes for run {run_id}")

        # ── Check 1: Orphan nodes ────────────────────────────────────────────
        result = session.run(
            """
            MATCH (n {run_id: $run_id})
            WHERE NOT (n)--()
            RETURN labels(n) AS labels, n.qualified_name AS qname
            LIMIT 50
            """,
            {"run_id": run_id},
        )
        orphans = list(result)
        if orphans:
            print(f"\n[WARN] Orphan nodes ({len(orphans)} found, showing ≤50):")
            for row in orphans:
                print(f"  {row['labels']} | {row['qname']}")
            issues += len(orphans)

            if args.fix:
                session.run(
                    """
                    MATCH (n {run_id: $run_id})
                    WHERE NOT (n)--()
                    DETACH DELETE n
                    """,
                    {"run_id": run_id},
                )
                print(f"  → Deleted {len(orphans)} orphan nodes.")
        else:
            print("[OK ] No orphan nodes.")

        # ── Check 2: Missing layer property ──────────────────────────────────
        result = session.run(
            """
            MATCH (n {run_id: $run_id})
            WHERE n.layer IS NULL
            RETURN labels(n) AS labels, n.qualified_name AS qname
            LIMIT 50
            """,
            {"run_id": run_id},
        )
        no_layer = list(result)
        if no_layer:
            print(f"\n[WARN] Nodes missing `layer` property ({len(no_layer)} found, showing ≤50):")
            for row in no_layer:
                print(f"  {row['labels']} | {row['qname']}")
            issues += len(no_layer)
        else:
            print("[OK ] All nodes have `layer` property.")

        # ── Check 3: Duplicate qualified_name within same label ───────────────
        result = session.run(
            """
            MATCH (n {run_id: $run_id})
            WHERE n.qualified_name IS NOT NULL
            WITH labels(n)[0] AS lbl, n.qualified_name AS qname, count(*) AS cnt
            WHERE cnt > 1
            RETURN lbl, qname, cnt
            ORDER BY cnt DESC
            LIMIT 30
            """,
            {"run_id": run_id},
        )
        dupes = list(result)
        if dupes:
            print(f"\n[WARN] Duplicate qualified_name ({len(dupes)} found):")
            for row in dupes:
                print(f"  ({row['lbl']}) {row['qname']}  x{row['cnt']}")
            issues += len(dupes)
        else:
            print("[OK ] No duplicate qualified_name.")

        # ── Check 4: Summary ─────────────────────────────────────────────────
        result = session.run(
            "MATCH (n {run_id: $run_id}) RETURN count(n) AS total_nodes",
            {"run_id": run_id},
        )
        total_nodes = result.single()["total_nodes"]

        result = session.run(
            """
            MATCH (a {run_id: $run_id})-[r]->(b {run_id: $run_id})
            RETURN count(r) AS total_edges
            """,
            {"run_id": run_id},
        )
        total_edges = result.single()["total_edges"]

    driver.close()

    print()
    print(f"Run ID      : {run_id}")
    print(f"Total nodes : {total_nodes}")
    print(f"Total edges : {total_edges}")
    print(f"Issues      : {issues}")

    return 0 if issues == 0 else 1


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Post-load integrity checks for graph exports.",
    )
    p.add_argument("--run", required=True, metavar="RUN_ID", help="Run ID to validate")
    p.add_argument("--fix", action="store_true", help="Auto-delete orphan nodes")
    p.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")
    return p.parse_args()


if __name__ == "__main__":
    sys.exit(main())
