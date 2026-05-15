"""IndexingStateStore: PostgreSQL repository for indexing jobs, files, and knowledge data."""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from typing import Any

import psycopg2
import psycopg2.extras
from psycopg2.pool import ThreadedConnectionPool

from ingest.models import IndexJobConfig

logger = logging.getLogger("indexing")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class _PgConn:
    """Context manager wrapping a pooled psycopg2 connection.

    Exposes a sqlite3-compatible interface:
      - execute(sql, params) -> cursor  (? placeholders are auto-converted to %s)
      - executescript(sql)   -> executes semicolon-separated statements
    Commits on clean exit, rolls back on exception, and returns the connection
    to the pool in both cases.
    """

    __slots__ = ("_pool", "_conn")

    def __init__(self, pool: ThreadedConnectionPool) -> None:
        self._pool = pool
        self._conn: Any = None

    def __enter__(self) -> "_PgConn":
        self._conn = self._pool.getconn()
        return self

    def __exit__(self, exc_type: Any, *_: Any) -> None:
        try:
            if exc_type:
                self._conn.rollback()
            else:
                self._conn.commit()
        finally:
            self._pool.putconn(self._conn)

    def execute(self, sql: str, params: tuple = ()) -> Any:
        cur = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql.replace("?", "%s"), params)
        return cur

    def executescript(self, sql: str) -> None:
        """Execute multiple semicolon-separated SQL statements."""
        cur = self._conn.cursor()
        for stmt in sql.split(";"):
            stmt = stmt.strip()
            if stmt:
                cur.execute(stmt)


class IndexingStateStore:
    def __init__(self, db_url: str) -> None:
        self._pool = ThreadedConnectionPool(2, 20, dsn=db_url)
        self._write_lock = threading.Lock()
        self._init_db()

    def _connect(self) -> _PgConn:
        return _PgConn(self._pool)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS index_jobs (
                    id BIGSERIAL PRIMARY KEY,
                    root_path TEXT NOT NULL,
                    repository TEXT DEFAULT '',
                    repository_path TEXT DEFAULT '',
                    repository_url_base TEXT DEFAULT '',
                    extensions TEXT DEFAULT '[]',
                    index_mode TEXT DEFAULT 'both',
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    total_files INTEGER DEFAULT 0,
                    queued_files INTEGER DEFAULT 0,
                    processed_files INTEGER DEFAULT 0,
                    indexed_files INTEGER DEFAULT 0,
                    updated_files INTEGER DEFAULT 0,
                    skipped_files INTEGER DEFAULT 0,
                    failed_files INTEGER DEFAULT 0,
                    current_file TEXT,
                    last_error TEXT,
                    cancel_requested INTEGER DEFAULT 0
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS job_files (
                    job_id BIGINT NOT NULL,
                    file_path TEXT NOT NULL,
                    rel_path TEXT NOT NULL,
                    mtime DOUBLE PRECISION NOT NULL,
                    size BIGINT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER DEFAULT 0,
                    last_error TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (job_id, file_path)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS indexed_files (
                    file_path TEXT PRIMARY KEY,
                    rel_path TEXT NOT NULL,
                    mtime DOUBLE PRECISION NOT NULL,
                    size BIGINT NOT NULL,
                    content_hash TEXT NOT NULL,
                    solr_id TEXT NOT NULL,
                    repository TEXT DEFAULT '',
                    repository_path TEXT DEFAULT '',
                    last_indexed_at TEXT NOT NULL,
                    last_status TEXT NOT NULL,
                    last_error TEXT
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_jobs_status ON index_jobs(status)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_job_files_status ON job_files(job_id, status)"
            )
            conn.execute("""
                CREATE TABLE IF NOT EXISTS knowledge_nodes (
                    id BIGSERIAL PRIMARY KEY,
                    label TEXT NOT NULL,
                    name TEXT NOT NULL,
                    repository TEXT NOT NULL DEFAULT 'manual',
                    properties_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS knowledge_edges (
                    id BIGSERIAL PRIMARY KEY,
                    from_qname TEXT NOT NULL,
                    to_qname TEXT NOT NULL,
                    rel_type TEXT NOT NULL,
                    properties_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS knowledge_texts (
                    id BIGSERIAL PRIMARY KEY,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    tags TEXT NOT NULL DEFAULT '',
                    repository TEXT NOT NULL DEFAULT 'knowledge',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    indexed_at TEXT
                )
            """)
            # Migrations: add columns that may be missing in existing deployments
            rows = conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='index_jobs' AND table_schema='public'"
            ).fetchall()
            existing = {row["column_name"] for row in rows}
            if "repository_url_base" not in existing:
                conn.execute(
                    "ALTER TABLE index_jobs ADD COLUMN repository_url_base TEXT DEFAULT ''"
                )
            if "index_mode" not in existing:
                conn.execute(
                    "ALTER TABLE index_jobs ADD COLUMN index_mode TEXT DEFAULT 'both'"
                )

    # ── Job recovery ───────────────────────────────────────────────────────────

    def recover_interrupted_jobs(self) -> None:
        with self._write_lock, self._connect() as conn:
            conn.execute(
                "UPDATE job_files SET status='pending', updated_at=? WHERE status='processing'",
                (utc_now(),),
            )
            conn.execute(
                """
                UPDATE index_jobs
                SET status='queued', started_at=NULL
                WHERE status='scanning' AND cancel_requested = 0
                """
            )
            conn.execute(
                """
                UPDATE index_jobs
                SET status='running', started_at=COALESCE(started_at, ?)
                WHERE status='running' AND cancel_requested = 0
                """,
                (utc_now(),),
            )

    # ── Job queries ────────────────────────────────────────────────────────────

    def get_active_job(self) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("""
                SELECT * FROM index_jobs
                WHERE status IN ('queued', 'scanning', 'running')
                ORDER BY id DESC LIMIT 1
                """).fetchone()
            return dict(row) if row else None

    def get_pending_job_for_source(
        self, root_path: str, repository_path: str
    ) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM index_jobs
                WHERE status IN ('queued', 'scanning', 'running')
                  AND root_path = ? AND repository_path = ?
                ORDER BY id DESC LIMIT 1
                """,
                (root_path, repository_path),
            ).fetchone()
            return dict(row) if row else None

    def create_job(self, config: IndexJobConfig) -> int:
        with self._write_lock, self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO index_jobs (
                    root_path, repository, repository_path, repository_url_base,
                    extensions, index_mode, status, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, 'queued', ?)
                RETURNING id
                """,
                (
                    config.root_path,
                    config.repository,
                    config.repository_path,
                    config.repository_url_base,
                    json.dumps(config.extensions),
                    config.index_mode,
                    utc_now(),
                ),
            )
            return int(cur.fetchone()["id"])

    def get_job(self, job_id: int) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM index_jobs WHERE id=?", (job_id,)
            ).fetchone()
            return dict(row) if row else None

    def set_job_status(self, job_id: int, status: str, error: str = "") -> None:
        now = utc_now()
        with self._write_lock, self._connect() as conn:
            if status in {"running", "scanning"}:
                conn.execute(
                    """
                    UPDATE index_jobs
                    SET status=?, started_at=COALESCE(started_at, ?), last_error=?
                    WHERE id=?
                    """,
                    (status, now, error, job_id),
                )
                return
            if status in {"completed", "cancelled", "failed"}:
                conn.execute(
                    """
                    UPDATE index_jobs
                    SET status=?, finished_at=?, last_error=?, current_file=NULL
                    WHERE id=?
                    """,
                    (status, now, error, job_id),
                )
                return
            conn.execute(
                "UPDATE index_jobs SET status=?, last_error=? WHERE id=?",
                (status, error, job_id),
            )

    def request_cancel(self, job_id: int) -> None:
        with self._write_lock, self._connect() as conn:
            conn.execute(
                "UPDATE index_jobs SET cancel_requested=1 WHERE id=?", (job_id,)
            )

    def list_recent_jobs(self, limit: int = 10) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM index_jobs WHERE status NOT IN ('running', 'scanning') ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    # ── Job file operations ────────────────────────────────────────────────────

    def clear_job_files(self, job_id: int) -> None:
        with self._write_lock, self._connect() as conn:
            conn.execute("DELETE FROM job_files WHERE job_id=?", (job_id,))

    def upsert_job_file(
        self,
        job_id: int,
        file_path: str,
        rel_path: str,
        mtime: float,
        size: int,
        status: str,
    ) -> None:
        with self._write_lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO job_files (job_id, file_path, rel_path, mtime, size, status, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id, file_path)
                DO UPDATE SET
                    rel_path=excluded.rel_path,
                    mtime=excluded.mtime,
                    size=excluded.size,
                    status=excluded.status,
                    updated_at=excluded.updated_at
                """,
                (job_id, file_path, rel_path, mtime, size, status, utc_now()),
            )

    def get_next_pending_file(self, job_id: int) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM job_files
                WHERE job_id=? AND status='pending'
                ORDER BY file_path ASC LIMIT 1
                """,
                (job_id,),
            ).fetchone()
            return dict(row) if row else None

    def mark_processing(self, job_id: int, file_path: str) -> None:
        with self._write_lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE job_files
                SET status='processing', attempts=attempts+1, updated_at=?
                WHERE job_id=? AND file_path=?
                """,
                (utc_now(), job_id, file_path),
            )
            conn.execute(
                "UPDATE index_jobs SET current_file=? WHERE id=?",
                (file_path, job_id),
            )

    def mark_processed(
        self,
        job_id: int,
        file_path: str,
        ok: bool,
        was_update: bool,
        error: str = "",
    ) -> None:
        with self._write_lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE job_files SET status=?, last_error=?, updated_at=?
                WHERE job_id=? AND file_path=?
                """,
                ("done" if ok else "failed", error or None, utc_now(), job_id, file_path),
            )
            if ok:
                col = "updated_files" if was_update else "indexed_files"
                conn.execute(
                    f"""
                    UPDATE index_jobs
                    SET processed_files=processed_files+1,
                        {col}={col}+1,
                        current_file=NULL
                    WHERE id=?
                    """,
                    (job_id,),
                )
            else:
                conn.execute(
                    """
                    UPDATE index_jobs
                    SET processed_files=processed_files+1,
                        failed_files=failed_files+1,
                        current_file=NULL,
                        last_error=?
                    WHERE id=?
                    """,
                    (error, job_id),
                )

    def has_pending_files(self, job_id: int) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM job_files WHERE job_id=? AND status IN ('pending', 'processing')",
                (job_id,),
            ).fetchone()
            return bool(row and row["cnt"] > 0)

    def set_job_scan_stats(
        self, job_id: int, total: int, queued: int, skipped: int
    ) -> None:
        with self._write_lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE index_jobs
                SET total_files=?, queued_files=?, skipped_files=?,
                    processed_files=0, indexed_files=0, updated_files=0,
                    failed_files=0, current_file=NULL
                WHERE id=?
                """,
                (total, queued, skipped, job_id),
            )

    # ── Indexed file registry ──────────────────────────────────────────────────

    def get_indexed_file(self, file_path: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM indexed_files WHERE file_path=?", (file_path,)
            ).fetchone()
            return dict(row) if row else None

    def save_indexed_file(
        self,
        file_path: str,
        rel_path: str,
        mtime: float,
        size: int,
        content_hash: str,
        solr_id: str,
        repository: str,
        repository_path: str,
        status: str,
        error: str = "",
    ) -> None:
        with self._write_lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO indexed_files (
                    file_path, rel_path, mtime, size, content_hash, solr_id,
                    repository, repository_path, last_indexed_at, last_status, last_error
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(file_path) DO UPDATE SET
                    rel_path=excluded.rel_path,
                    mtime=excluded.mtime,
                    size=excluded.size,
                    content_hash=excluded.content_hash,
                    solr_id=excluded.solr_id,
                    repository=excluded.repository,
                    repository_path=excluded.repository_path,
                    last_indexed_at=excluded.last_indexed_at,
                    last_status=excluded.last_status,
                    last_error=excluded.last_error
                """,
                (
                    file_path, rel_path, mtime, size, content_hash, solr_id,
                    repository, repository_path, utc_now(), status, error or None,
                ),
            )

    def get_indexed_files_for_repo(
        self, repository: str, repository_path: str
    ) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM indexed_files WHERE repository=? AND repository_path=?",
                (repository, repository_path),
            ).fetchall()
            return [dict(r) for r in rows]

    def delete_indexed_file(self, file_path: str) -> None:
        with self._write_lock, self._connect() as conn:
            conn.execute("DELETE FROM indexed_files WHERE file_path=?", (file_path,))

    def list_recent_indexed_files(self, limit: int = 30) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT file_path, rel_path, repository, last_indexed_at, last_status, last_error
                FROM indexed_files ORDER BY last_indexed_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def clear_all_data(self) -> None:
        """Wipe all indexing history: jobs, job_files, indexed_files."""
        with self._write_lock, self._connect() as conn:
            conn.execute("DELETE FROM job_files")
            conn.execute("DELETE FROM index_jobs")
            conn.execute("DELETE FROM indexed_files")

    # ── Knowledge CRUD ─────────────────────────────────────────────────────────
    # These methods provide PostgreSQL access for KnowledgeService.
    # The knowledge_* tables are owned by KnowledgeService — do not add
    # business logic here.

    def list_knowledge_nodes(self, label: str = "", q: str = "", limit: int = 200) -> list[dict]:
        sql = "SELECT * FROM knowledge_nodes WHERE 1=1"
        params: list = []
        if label:
            sql += " AND label=?"
            params.append(label)
        if q:
            sql += " AND (name LIKE ? OR properties_json LIKE ?)"
            params.extend([f"%{q}%", f"%{q}%"])
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]

    def create_knowledge_node(self, label: str, name: str, repository: str, properties: dict) -> int:
        now = utc_now()
        with self._write_lock, self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO knowledge_nodes (label, name, repository, properties_json, created_at, updated_at) VALUES (?,?,?,?,?,?) RETURNING id",
                (label, name, repository, json.dumps(properties), now, now),
            )
            return int(cur.fetchone()["id"])

    def update_knowledge_node(self, node_id: int, name: str, repository: str, properties: dict) -> bool:
        with self._write_lock, self._connect() as conn:
            cur = conn.execute(
                "UPDATE knowledge_nodes SET name=?, repository=?, properties_json=?, updated_at=? WHERE id=?",
                (name, repository, json.dumps(properties), utc_now(), node_id),
            )
            return cur.rowcount > 0

    def delete_knowledge_node(self, node_id: int) -> bool:
        with self._write_lock, self._connect() as conn:
            cur = conn.execute("DELETE FROM knowledge_nodes WHERE id=?", (node_id,))
            return cur.rowcount > 0

    def upsert_knowledge_node(self, label: str, name: str, repository: str, properties: dict | None = None) -> int:
        now = utc_now()
        props_json = json.dumps(properties or {})
        with self._write_lock, self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM knowledge_nodes WHERE label=? AND name=? AND repository=?",
                (label, name, repository),
            ).fetchone()
            if row:
                return int(row["id"])
            cur = conn.execute(
                "INSERT INTO knowledge_nodes (label, name, repository, properties_json, created_at, updated_at) VALUES (?,?,?,?,?,?) RETURNING id",
                (label, name, repository, props_json, now, now),
            )
            return int(cur.fetchone()["id"])

    def list_knowledge_edges(self, rel_type: str = "", q: str = "", limit: int = 200) -> list[dict]:
        sql = "SELECT * FROM knowledge_edges WHERE 1=1"
        params: list = []
        if rel_type:
            sql += " AND rel_type=?"
            params.append(rel_type)
        if q:
            sql += " AND (from_qname LIKE ? OR to_qname LIKE ?)"
            params.extend([f"%{q}%", f"%{q}%"])
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]

    def create_knowledge_edge(self, from_qname: str, to_qname: str, rel_type: str, properties: dict) -> int:
        with self._write_lock, self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO knowledge_edges (from_qname, to_qname, rel_type, properties_json, created_at) VALUES (?,?,?,?,?) RETURNING id",
                (from_qname, to_qname, rel_type, json.dumps(properties), utc_now()),
            )
            return int(cur.fetchone()["id"])

    def delete_knowledge_edge(self, edge_id: int) -> bool:
        with self._write_lock, self._connect() as conn:
            cur = conn.execute("DELETE FROM knowledge_edges WHERE id=?", (edge_id,))
            return cur.rowcount > 0

    def list_knowledge_texts(self, q: str = "", tags: str = "", limit: int = 200) -> list[dict]:
        sql = "SELECT id, title, tags, repository, created_at, updated_at, indexed_at, substr(content,1,200) AS preview FROM knowledge_texts WHERE 1=1"
        params: list = []
        if q:
            sql += " AND (title LIKE ? OR content LIKE ?)"
            params.extend([f"%{q}%", f"%{q}%"])
        if tags:
            for tag in tags.split(","):
                t = tag.strip()
                if t:
                    sql += " AND tags LIKE ?"
                    params.append(f"%{t}%")
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]

    def get_knowledge_text(self, text_id: int) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM knowledge_texts WHERE id=?", (text_id,)).fetchone()
            return dict(row) if row else None

    def create_knowledge_text(self, title: str, content: str, tags: str, repository: str) -> int:
        now = utc_now()
        with self._write_lock, self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO knowledge_texts (title, content, tags, repository, created_at, updated_at) VALUES (?,?,?,?,?,?) RETURNING id",
                (title, content, tags, repository, now, now),
            )
            return int(cur.fetchone()["id"])

    def update_knowledge_text(self, text_id: int, title: str, content: str, tags: str, repository: str) -> bool:
        with self._write_lock, self._connect() as conn:
            cur = conn.execute(
                "UPDATE knowledge_texts SET title=?, content=?, tags=?, repository=?, updated_at=? WHERE id=?",
                (title, content, tags, repository, utc_now(), text_id),
            )
            return cur.rowcount > 0

    def mark_knowledge_text_indexed(self, text_id: int) -> None:
        with self._write_lock, self._connect() as conn:
            conn.execute("UPDATE knowledge_texts SET indexed_at=? WHERE id=?", (utc_now(), text_id))

    def delete_knowledge_text(self, text_id: int) -> bool:
        with self._write_lock, self._connect() as conn:
            cur = conn.execute("DELETE FROM knowledge_texts WHERE id=?", (text_id,))
            return cur.rowcount > 0
