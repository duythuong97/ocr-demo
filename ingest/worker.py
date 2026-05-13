"""IndexingWorker: background thread that processes index jobs."""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import config as cfg
from infra.qdrant_store import QdrantStore
from infra.solr_store import SolrStore
from ingest.readers.factory import ReaderFactory
from ingest.store import IndexingStateStore

logger = logging.getLogger("indexing")


class IndexingWorker:
    _SKIP_DIRS: frozenset[str] = frozenset(
        {
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
    )
    # Windows metadata files that should never be indexed
    _SKIP_FILES: frozenset[str] = frozenset(
        {"Thumbs.db", "desktop.ini", "ehthumbs.db", "ehthumbs_vista.db"}
    )
    _CODE_EXTENSIONS: frozenset[str] = frozenset(
        {
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
    )

    def __init__(
        self,
        store: IndexingStateStore,
        solr: SolrStore,
        semantic_service: QdrantStore | None = None,
        on_change: Callable[[], None] | None = None,
        knowledge_service: Any | None = None,
    ):
        self.store = store
        self.solr = solr
        self.semantic_service = semantic_service
        self._on_change = on_change
        self.reader_factory = ReaderFactory()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._knowledge_service = knowledge_service

    def _notify(self) -> None:
        if self._on_change:
            try:
                self._on_change()
            except Exception:
                logger.warning("on_change callback raised an exception", exc_info=True)

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
                self._post_job_hooks(job_id)
                self._notify()
                continue

            file_path = item["file_path"]
            self.store.mark_processing(job_id, file_path)
            self._index_one_file(active, item)
            self._notify()

    def _post_job_hooks(self, job_id: int) -> None:
        """Run post-ingest hooks: re-apply manual knowledge + cross-file graph resolution."""
        if self._knowledge_service is not None:
            try:
                self._knowledge_service.apply_all()
                logger.info("Job %d: knowledge re-applied to Neo4j/Solr/Qdrant", job_id)
            except Exception as exc:
                logger.warning("Knowledge apply failed (non-fatal): %s", exc)

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

        total = 0
        queued = 0
        skipped = 0

        scanned_paths: set[str] = set()

        for path in scan_root.rglob("*"):
            if not path.is_file():
                continue

            # Skip hidden directories, hidden files (e.g. .DS_Store),
            # Windows metadata files, and common bulk/generated directories
            rel_parts = path.relative_to(scan_root).parts
            if path.name in self._SKIP_FILES or any(
                part.startswith(".") or part in self._SKIP_DIRS
                for part in rel_parts
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
                        self.solr.delete_by_id(solr_id)
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
                self.solr.index_doc(doc)
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
                    semantic_text = reader.read_semantic(source) or content
                    raw_chars = len(semantic_text)

                    max_chars = int(getattr(cfg, "SEMANTIC_MAX_TEXT_CHARS", 0) or 0)
                    if max_chars > 0 and raw_chars > max_chars:
                        logger.warning(
                            "  semantic: text is large (%d chars > %d limit); "
                            "chunking will proceed — SEMANTIC_MAX_CHUNKS_PER_FILE caps output chunks",
                            raw_chars,
                            max_chars,
                        )

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
