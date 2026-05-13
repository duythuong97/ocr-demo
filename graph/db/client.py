"""Neo4j driver singleton with lazy connection and graceful degradation."""
from __future__ import annotations

import logging
import threading
from typing import Any

try:
    from neo4j import GraphDatabase as _GraphDatabase
    _NEO4J_AVAILABLE = True
except ImportError:
    _GraphDatabase = None  # type: ignore[assignment]
    _NEO4J_AVAILABLE = False

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_instance: "GraphClient | None" = None


class GraphClient:
    """Thin wrapper around the neo4j Python driver.

    Instantiate once and reuse.  Falls back to a no-op stub when neo4j-driver
    is not installed or the server is unreachable.
    """

    def __init__(self, url: str, user: str, password: str, database: str = "neo4j") -> None:
        self.url = url
        self.user = user
        self.database = database
        self._driver = None
        self._available = False

        if not _NEO4J_AVAILABLE:
            logger.warning(
                "neo4j driver not installed — graph pipeline disabled. "
                "Run: pip install neo4j"
            )
            return
        try:
            self._driver = _GraphDatabase.driver(url, auth=(user, password))
            # Verify connectivity
            self._driver.verify_connectivity()
            self._available = True
            logger.info("Neo4j connected at %s (db=%s)", url, database)
        except Exception as exc:
            logger.warning("Neo4j not reachable at %s: %s — graph pipeline disabled", url, exc)

    @property
    def available(self) -> bool:
        return self._available

    def run(self, cypher: str, parameters: dict[str, Any] | None = None) -> list[dict]:
        """Execute a Cypher query and return rows as dicts."""
        if not self._available or self._driver is None:
            return []
        params = parameters or {}
        try:
            with self._driver.session(database=self.database) as session:
                result = session.run(cypher, params)
                return [dict(record) for record in result]
        except Exception as exc:
            logger.error("Neo4j query error: %s\nCypher: %s", exc, cypher[:200])
            return []

    def run_write(self, cypher: str, parameters: dict[str, Any] | None = None) -> None:
        """Execute a write Cypher statement, swallowing errors gracefully."""
        if not self._available or self._driver is None:
            return
        params = parameters or {}
        try:
            with self._driver.session(database=self.database) as session:
                session.execute_write(lambda tx: tx.run(cypher, params))
        except Exception as exc:
            logger.error("Neo4j write error: %s\nCypher: %s", exc, cypher[:200])

    def run_write_raise(self, cypher: str, parameters: dict[str, Any] | None = None) -> None:
        """Execute a write Cypher statement, raising on error."""
        if not self._available or self._driver is None:
            raise RuntimeError("Neo4j client not available")
        params = parameters or {}
        with self._driver.session(database=self.database) as session:
            session.execute_write(lambda tx: tx.run(cypher, params))

    def close(self) -> None:
        if self._driver:
            try:
                self._driver.close()
            except Exception as exc:
                logger.warning("Error closing Neo4j driver: %s", exc)


def get_client() -> "GraphClient | None":
    """Return the module-level singleton, or None if not yet initialised."""
    return _instance


def init_client(url: str, user: str, password: str, database: str = "neo4j") -> GraphClient:
    """Initialise the singleton client (call once at app startup)."""
    global _instance
    with _lock:
        if _instance is None:
            _instance = GraphClient(url, user, password, database)
        return _instance
