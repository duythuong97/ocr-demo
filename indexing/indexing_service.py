from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("indexing")

import pysolr

import config as cfg
from indexing.readers import ReaderFactory
from indexing.semantic_service import SemanticSearchService


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class IndexJobConfig:
    root_path: str
    repository: str
    repository_path: str
    repository_url_base: str
    extensions: list[str]
    index_mode: str = "both"  # "both" | "solr" | "semantic"


class IndexingStateStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._write_lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS index_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    root_path TEXT NOT NULL,
                    repository TEXT DEFAULT '',
                    repository_path TEXT DEFAULT '',
                    repository_url_base TEXT DEFAULT '',
                    extensions TEXT DEFAULT '[]',
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
                );

                CREATE TABLE IF NOT EXISTS job_files (
                    job_id INTEGER NOT NULL,
                    file_path TEXT NOT NULL,
                    rel_path TEXT NOT NULL,
                    mtime REAL NOT NULL,
                    size INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER DEFAULT 0,
                    last_error TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (job_id, file_path)
                );

                CREATE TABLE IF NOT EXISTS indexed_files (
                    file_path TEXT PRIMARY KEY,
                    rel_path TEXT NOT NULL,
                    mtime REAL NOT NULL,
                    size INTEGER NOT NULL,
                    content_hash TEXT NOT NULL,
                    solr_id TEXT NOT NULL,
                    repository TEXT DEFAULT '',
                    repository_path TEXT DEFAULT '',
                    last_indexed_at TEXT NOT NULL,
                    last_status TEXT NOT NULL,
                    last_error TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_jobs_status ON index_jobs(status);
                CREATE INDEX IF NOT EXISTS idx_job_files_status ON job_files(job_id, status);
                """
            )
            # Migrate: add repository_url_base column if it doesn't exist yet
            existing = {
                row[1]
                for row in conn.execute("PRAGMA table_info(index_jobs)").fetchall()
            }
            if "repository_url_base" not in existing:
                conn.execute(
                    "ALTER TABLE index_jobs ADD COLUMN repository_url_base TEXT DEFAULT ''"
                )
            if "index_mode" not in existing:
                conn.execute(
                    "ALTER TABLE index_jobs ADD COLUMN index_mode TEXT DEFAULT 'both'"
                )

    def recover_interrupted_jobs(self) -> None:
        with self._write_lock, self._connect() as conn:
            # Files left in 'processing' from a crashed run → reset to pending
            conn.execute(
                "UPDATE job_files SET status='pending', updated_at=? WHERE status='processing'",
                (utc_now(),),
            )
            # Jobs that were mid-scan when killed → re-queue so they get a fresh scan
            conn.execute(
                """
                UPDATE index_jobs
                SET status='queued', started_at=NULL
                WHERE status='scanning'
                  AND cancel_requested = 0
                """,
                (),
            )
            # Jobs that were mid-run → resume them
            conn.execute(
                """
                UPDATE index_jobs
                SET status='running', started_at=COALESCE(started_at, ?)
                WHERE status='running'
                  AND cancel_requested = 0
                """,
                (utc_now(),),
            )

    def get_active_job(self) -> sqlite3.Row | None:
        with self._connect() as conn:
            return conn.execute(
                """
                SELECT *
                FROM index_jobs
                WHERE status IN ('queued', 'scanning', 'running')
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()

    def get_pending_job_for_source(
        self, root_path: str, repository_path: str
    ) -> sqlite3.Row | None:
        """Return any queued/scanning/running job for the same source folder."""
        with self._connect() as conn:
            return conn.execute(
                """
                SELECT *
                FROM index_jobs
                WHERE status IN ('queued', 'scanning', 'running')
                  AND root_path = ?
                  AND repository_path = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (root_path, repository_path),
            ).fetchone()

    def create_job(self, config: IndexJobConfig) -> int:
        with self._write_lock, self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO index_jobs (
                    root_path, repository, repository_path, repository_url_base,
                    extensions, index_mode, status, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, 'queued', ?)
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
            return int(cur.lastrowid)

    def get_job(self, job_id: int) -> sqlite3.Row | None:
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM index_jobs WHERE id=?",
                (job_id,),
            ).fetchone()

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
                "UPDATE index_jobs SET cancel_requested=1 WHERE id=?",
                (job_id,),
            )

    def clear_job_files(self, job_id: int) -> None:
        with self._write_lock, self._connect() as conn:
            conn.execute("DELETE FROM job_files WHERE job_id=?", (job_id,))

    def clear_all_data(self) -> None:
        """Wipe all indexing history: jobs, job_files, indexed_files."""
        with self._write_lock, self._connect() as conn:
            conn.executescript(
                "DELETE FROM job_files; DELETE FROM index_jobs; DELETE FROM indexed_files;"
            )

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

    def get_indexed_file(self, file_path: str) -> sqlite3.Row | None:
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM indexed_files WHERE file_path=?",
                (file_path,),
            ).fetchone()

    def set_job_scan_stats(
        self, job_id: int, total: int, queued: int, skipped: int
    ) -> None:
        with self._write_lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE index_jobs
                SET total_files=?, queued_files=?, skipped_files=?, processed_files=0,
                    indexed_files=0, updated_files=0, failed_files=0, current_file=NULL
                WHERE id=?
                """,
                (total, queued, skipped, job_id),
            )

    def get_next_pending_file(self, job_id: int) -> sqlite3.Row | None:
        with self._connect() as conn:
            return conn.execute(
                """
                SELECT *
                FROM job_files
                WHERE job_id=? AND status='pending'
                ORDER BY file_path ASC
                LIMIT 1
                """,
                (job_id,),
            ).fetchone()

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
                UPDATE job_files
                SET status=?, last_error=?, updated_at=?
                WHERE job_id=? AND file_path=?
                """,
                (
                    "done" if ok else "failed",
                    error or None,
                    utc_now(),
                    job_id,
                    file_path,
                ),
            )

            if ok:
                if was_update:
                    conn.execute(
                        """
                        UPDATE index_jobs
                        SET processed_files=processed_files+1,
                            updated_files=updated_files+1,
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
                            indexed_files=indexed_files+1,
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
                """
                SELECT COUNT(*) AS cnt
                FROM job_files
                WHERE job_id=? AND status IN ('pending', 'processing')
                """,
                (job_id,),
            ).fetchone()
            return bool(row and row["cnt"] > 0)

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
                    repository, repository_path, last_indexed_at,
                    last_status, last_error
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(file_path)
                DO UPDATE SET
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
                    file_path,
                    rel_path,
                    mtime,
                    size,
                    content_hash,
                    solr_id,
                    repository,
                    repository_path,
                    utc_now(),
                    status,
                    error or None,
                ),
            )

    def get_indexed_files_for_repo(
        self, repository: str, repository_path: str
    ) -> list[sqlite3.Row]:
        """Return all indexed_files rows for a given repository."""
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM indexed_files WHERE repository=? AND repository_path=?",
                (repository, repository_path),
            ).fetchall()

    def delete_indexed_file(self, file_path: str) -> None:
        with self._write_lock, self._connect() as conn:
            conn.execute(
                "DELETE FROM indexed_files WHERE file_path=?",
                (file_path,),
            )

    def list_recent_jobs(self, limit: int = 10) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM index_jobs ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def list_recent_indexed_files(self, limit: int = 30) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                  SELECT file_path, rel_path, repository, last_indexed_at,
                       last_status, last_error
                FROM indexed_files
                ORDER BY last_indexed_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]


class IndexingWorker:
    def __init__(
        self,
        store: IndexingStateStore,
        solr: pysolr.Solr,
        semantic_service: SemanticSearchService | None = None,
        on_change: Callable[[], None] | None = None,
    ):
        self.store = store
        self.solr = solr
        self.semantic_service = semantic_service
        self._on_change = on_change
        self.reader_factory = ReaderFactory()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def _notify(self) -> None:
        if self._on_change:
            try:
                self._on_change()
            except Exception:
                pass

    @property
    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> None:
        if self.is_running:
            return
        self.store.recover_interrupted_jobs()
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("IndexingWorker started")

    def stop(self) -> None:
        self._stop_event.set()
        logger.info("IndexingWorker stop requested")

    def _run(self) -> None:
        while not self._stop_event.is_set():
            active = self.store.get_active_job()
            if not active:
                time.sleep(1.0)
                continue

            job_id = int(active["id"])
            if active["status"] == "queued":
                self._scan_job(job_id)
                continue

            if int(active["cancel_requested"] or 0) == 1:
                logger.info("Job %d cancelled by user", job_id)
                self.store.set_job_status(job_id, "cancelled")
                self._notify()
                continue

            self.store.set_job_status(job_id, "running")
            item = self.store.get_next_pending_file(job_id)
            if not item:
                if self.store.has_pending_files(job_id):
                    time.sleep(0.5)
                    continue
                logger.info("Job %d completed", job_id)
                self.store.set_job_status(job_id, "completed")
                self._notify()
                continue

            file_path = item["file_path"]
            self.store.mark_processing(job_id, file_path)
            self._index_one_file(active, item)
            self._notify()

    def _scan_job(self, job_id: int) -> None:
        job = self.store.get_job(job_id)
        if not job:
            return

        self.store.set_job_status(job_id, "scanning")
        root = Path(job["root_path"]).expanduser().resolve()
        if not root.exists() or not root.is_dir():
            logger.error("Job %d: root path not found: %s", job_id, root)
            self.store.set_job_status(job_id, "failed", "Index root path not found")
            return

        # Scan from repository_path if specified, otherwise from root
        scan_root = root / job["repository_path"] if job["repository_path"] else root
        if not scan_root.exists() or not scan_root.is_dir():
            logger.error("Job %d: repository_path not found: %s", job_id, scan_root)
            self.store.set_job_status(
                job_id, "failed", f"Repository path not found: {scan_root}"
            )
            return

        logger.info("Job %d: scanning %s", job_id, scan_root)
        self.store.clear_job_files(job_id)

        exts = json.loads(job["extensions"] or "[]")
        ext_set = {ext.lower().lstrip(".") for ext in exts if ext}

        # Directories whose contents should never be indexed
        _SKIP_DIRS = {
            "node_modules",
            "__pycache__",
            "venv",
            "env",
            "virtualenv",
            "dist",
            "build",
            "target",
            "bin",
            "obj",
            "out",
            "output",
            "vendor",
            "packages",
            "bower_components",
            ".yarn",
            "coverage",
            ".coverage",
            "htmlcov",
            "site-packages",
            "lib64",
        }

        total = 0
        queued = 0
        skipped = 0

        scanned_paths: set[str] = set()

        for path in scan_root.rglob("*"):
            if not path.is_file():
                continue

            # Skip hidden directories and common bulk/generated directories
            rel_parts = path.relative_to(scan_root).parts
            if any(
                part.startswith(".") or part in _SKIP_DIRS for part in rel_parts[:-1]
            ):
                continue

            ext = path.suffix.lower().lstrip(".")
            if ext_set and ext not in ext_set:
                continue

            stat = path.stat()
            file_path = str(path.resolve())
            # Use POSIX (forward-slash) separators so rel_path is always
            # forward-slash regardless of OS — safe to embed in URLs/Qdrant payload.
            rel_path = path.relative_to(scan_root).as_posix()
            total += 1
            scanned_paths.add(file_path)

            known = self.store.get_indexed_file(file_path)
            needs_index = (
                known is None
                or float(known["mtime"]) != float(stat.st_mtime)
                or int(known["size"]) != int(stat.st_size)
                or str(known["last_status"]) != "done"
            )

            status = "pending" if needs_index else "skipped"
            self.store.upsert_job_file(
                job_id=job_id,
                file_path=file_path,
                rel_path=rel_path,
                mtime=float(stat.st_mtime),
                size=int(stat.st_size),
                status=status,
            )
            if needs_index:
                queued += 1
            else:
                skipped += 1

        # ── Stale file cleanup ───────────────────────────────────────────────
        # Find files previously indexed for this repo that no longer exist on disk
        index_mode = str(job["index_mode"] or "both")
        previously_indexed = self.store.get_indexed_files_for_repo(
            repository=str(job["repository"] or ""),
            repository_path=str(job["repository_path"] or ""),
        )
        deleted_count = 0
        for row in previously_indexed:
            fp = str(row["file_path"])
            if fp in scanned_paths:
                continue
            logger.info("Job %d: removing stale file from index: %s", job_id, fp)
            try:
                if index_mode in ("both", "solr"):
                    solr_id = str(row["solr_id"])
                    if solr_id:
                        self.solr.delete(id=solr_id, commit=True)
            except Exception as exc:
                logger.warning("  stale cleanup Solr failed for %s: %s", fp, exc)
            try:
                if index_mode in ("both", "semantic") and self.semantic_service:
                    self.semantic_service.delete_file(fp)
            except Exception as exc:
                logger.warning("  stale cleanup Qdrant failed for %s: %s", fp, exc)
            self.store.delete_indexed_file(fp)
            deleted_count += 1

        if deleted_count:
            logger.info("Job %d: removed %d stale files", job_id, deleted_count)
        # ────────────────────────────────────────────────────────────────────

        self.store.set_job_scan_stats(
            job_id, total=total, queued=queued, skipped=skipped
        )
        logger.info(
            "Job %d scan complete: %d total, %d queued, %d skipped, %d stale removed",
            job_id,
            total,
            queued,
            skipped,
            deleted_count,
        )

        if queued == 0:
            logger.info("Job %d: nothing to index, completed immediately", job_id)
            self.store.set_job_status(job_id, "completed")
        else:
            self.store.set_job_status(job_id, "running")
        self._notify()

    def _index_one_file(self, job: sqlite3.Row, item: sqlite3.Row) -> None:
        file_path = str(item["file_path"])
        rel_path = str(item["rel_path"])

        logger.info("Indexing: %s", rel_path)
        t0 = time.perf_counter()
        try:
            source = Path(file_path)
            reader = self.reader_factory.get_reader(source)
            logger.debug("  reader: %s", type(reader).__name__)
            content = reader.read_content(source)

            if not content.strip():
                raise ValueError("No extractable text content")

            sha = hashlib.sha1(content.encode("utf-8")).hexdigest()
            ext = source.suffix.lower().lstrip(".")
            is_code = ext in {
                "py",
                "js",
                "ts",
                "tsx",
                "jsx",
                "java",
                "go",
                "cs",
                "cpp",
                "c",
                "rb",
                "php",
                "rs",
                "swift",
                "kt",
                "sql",
                "sh",
            }

            known = self.store.get_indexed_file(file_path)
            was_update = known is not None

            solr_id = f"local-{hashlib.sha1(file_path.encode('utf-8')).hexdigest()}"
            dt_utc = datetime.fromtimestamp(
                float(item["mtime"]), tz=timezone.utc
            ).strftime("%Y-%m-%dT%H:%M:%SZ")

            # Build repository URL: base + rel_path
            # rel_path is already relative to scan_root (= root_path/repository_path),
            # so it is relative to the repository root — do NOT add repository_path again.
            repository_url = ""
            url_base = (
                job["repository_url_base"]
                if "repository_url_base" in job.keys()
                else ""
            )
            if url_base:
                base = url_base.rstrip("/")
                rel = rel_path.replace("\\", "/")
                repository_url = f"{base}/{rel}"

            doc = {
                cfg.FIELD_ID: solr_id,
                cfg.FIELD_TITLE: source.name,
                cfg.FIELD_CONTENT: content,
                cfg.FIELD_FILE_TYPE: ext or "txt",
                cfg.FIELD_FILE_PATH: file_path,
                cfg.FIELD_REPOSITORY: (job["repository"] or source.parent.name),
                cfg.FIELD_REPOSITORY_PATH: (job["repository_url_base"] or ""),
                cfg.FIELD_DATE: dt_utc,
            }
            if repository_url:
                doc[cfg.FIELD_URL] = repository_url
            if is_code:
                doc[cfg.FIELD_CODE] = content

            index_mode = str(job["index_mode"] or "both")

            if index_mode in ("both", "solr"):
                t_solr0 = time.perf_counter()
                self.solr.add([doc], commit=True)
                logger.debug(
                    "  solr doc added: %s (%.2fs)",
                    solr_id,
                    time.perf_counter() - t_solr0,
                )
            else:
                logger.debug("  solr: skipped (index_mode=%s)", index_mode)

            if index_mode in ("both", "semantic") and self.semantic_service:
                skip_types = getattr(cfg, "SEMANTIC_SKIP_FILE_TYPES", set())
                if ext in skip_types:
                    logger.info("  semantic: skipped by file type (%s)", ext)
                else:
                    logger.debug("  semantic: start")
                    t_sem0 = time.perf_counter()
                    semantic_text = reader.read_sematic(source) or content
                    raw_chars = len(semantic_text)

                    max_chars = int(getattr(cfg, "SEMANTIC_MAX_TEXT_CHARS", 0) or 0)
                    if max_chars > 0 and raw_chars > max_chars:
                        logger.warning(
                            "  semantic: text too large (%d chars), truncating to %d chars",
                            raw_chars,
                            max_chars,
                        )
                        semantic_text = semantic_text[:max_chars]

                    logger.debug(
                        "  semantic: text extracted (chars=%d, %.2fs)",
                        len(semantic_text),
                        time.perf_counter() - t_sem0,
                    )

                    max_chunks = int(
                        getattr(cfg, "SEMANTIC_MAX_CHUNKS_PER_FILE", 0) or 0
                    )
                    t_upsert0 = time.perf_counter()
                    stats = self.semantic_service.upsert_file(
                        file_path=file_path,
                        rel_path=rel_path,
                        text=semantic_text,
                        repository=(job["repository"] or source.parent.name),
                        file_type=ext or "txt",
                        repository_url_base=(
                            job["repository_url_base"]
                            if "repository_url_base" in job.keys()
                            else ""
                        ),
                        max_chunks=max_chunks if max_chunks > 0 else None,
                    )
                    if stats.get("truncated_chunks"):
                        logger.warning(
                            "  semantic: chunks truncated %d -> %d",
                            int(stats.get("original_chunks", 0)),
                            int(stats.get("chunks", 0)),
                        )
                    logger.debug(
                        "  semantic: upsert done (%.2fs, total semantic %.2fs, chunks=%d)",
                        time.perf_counter() - t_upsert0,
                        time.perf_counter() - t_sem0,
                        int(stats.get("chunks", 0)),
                    )

            self.store.save_indexed_file(
                file_path=file_path,
                rel_path=rel_path,
                mtime=float(item["mtime"]),
                size=int(item["size"]),
                content_hash=sha,
                solr_id=solr_id,
                repository=(job["repository"] or source.parent.name),
                repository_path=(job["repository_path"] or ""),
                status="done",
            )
            self.store.mark_processed(
                job_id=int(item["job_id"]),
                file_path=file_path,
                ok=True,
                was_update=was_update,
            )
            logger.info(
                "  OK (%s): %s (%.2fs)",
                "updated" if was_update else "new",
                rel_path,
                time.perf_counter() - t0,
            )
        except Exception as exc:
            logger.error("  FAILED: %s — %s", rel_path, exc, exc_info=True)
            self.store.save_indexed_file(
                file_path=file_path,
                rel_path=rel_path,
                mtime=float(item["mtime"]),
                size=int(item["size"]),
                content_hash="",
                solr_id=f"local-{hashlib.sha1(file_path.encode('utf-8')).hexdigest()}",
                repository=str(job["repository"] or ""),
                repository_path=str(job["repository_path"] or ""),
                status="failed",
                error=str(exc),
            )
            self.store.mark_processed(
                job_id=int(item["job_id"]),
                file_path=file_path,
                ok=False,
                was_update=False,
                error=str(exc),
            )
