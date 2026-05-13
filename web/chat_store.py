"""ChatStore: persist chat history (messages + agent events) in SQLite.

Each browser session is identified by a UUID generated client-side and stored
in localStorage. The UUID is sent as the ``X-Session-ID`` header on every
request.

Schema (two tables added to the shared indexing.db):

  chat_sessions: id TEXT (UUID PK), created_at TEXT, last_active_at TEXT
  chat_messages: id INTEGER PK, session_id TEXT, seq INTEGER, role TEXT,
                 content TEXT, events TEXT (JSON), sources TEXT (JSON),
                 graph TEXT (JSON), created_at TEXT
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DDL = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS chat_sessions (
    id             TEXT PRIMARY KEY,
    created_at     TEXT NOT NULL,
    last_active_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT    NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    seq        INTEGER NOT NULL,
    role       TEXT    NOT NULL,
    content    TEXT    NOT NULL DEFAULT '',
    events     TEXT    NOT NULL DEFAULT '[]',
    sources    TEXT    NOT NULL DEFAULT '[]',
    graph      TEXT    NOT NULL DEFAULT '{}',
    created_at TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chat_messages_session
    ON chat_messages (session_id, seq);
"""


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


class ChatStore:
    """Thread-safe SQLite-backed chat history store."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._write_lock = threading.Lock()
        self._init_db()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_DDL)

    def _ensure_session(self, conn: sqlite3.Connection, session_id: str) -> None:
        """Create session row if it does not exist."""
        now = _utc()
        conn.execute(
            """
            INSERT INTO chat_sessions (id, created_at, last_active_at)
            VALUES (?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET last_active_at = excluded.last_active_at
            """,
            (session_id, now, now),
        )

    # ── Public API ────────────────────────────────────────────────────────────

    def get_history(self, session_id: str, limit: int = 100) -> list[dict]:
        """Return the last *limit* messages for *session_id*, oldest first."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT role, content, events, sources, graph, seq, created_at
                FROM chat_messages
                WHERE session_id = ?
                ORDER BY seq ASC
                LIMIT ?
                """,
                (session_id, limit),
            ).fetchall()
        return [
            {
                "role":       r["role"],
                "content":    r["content"],
                "events":     json.loads(r["events"]  or "[]"),
                "sources":    json.loads(r["sources"] or "[]"),
                "graph":      json.loads(r["graph"]   or "{}"),
                "seq":        r["seq"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    def append_message(
        self,
        session_id: str,
        role: str,
        content: str,
        events: list[dict] | None = None,
        sources: list[dict] | None = None,
        graph: dict | None = None,
    ) -> int:
        """Append one message to *session_id*. Returns the new message id."""
        with self._write_lock:
            with self._connect() as conn:
                self._ensure_session(conn, session_id)
                # Next seq number
                row = conn.execute(
                    "SELECT COALESCE(MAX(seq), -1) + 1 AS next_seq FROM chat_messages WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
                seq = row["next_seq"]
                cur = conn.execute(
                    """
                    INSERT INTO chat_messages
                        (session_id, seq, role, content, events, sources, graph, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session_id,
                        seq,
                        role,
                        content,
                        json.dumps(events  or [], ensure_ascii=False),
                        json.dumps(sources or [], ensure_ascii=False),
                        json.dumps(graph   or {}, ensure_ascii=False),
                        _utc(),
                    ),
                )
                return cur.lastrowid  # type: ignore[return-value]

    def list_sessions(self, limit: int = 50) -> list[dict]:
        """Return most-recently-active sessions with a title from first user message."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    s.id,
                    s.created_at,
                    s.last_active_at,
                    (SELECT m.content
                     FROM chat_messages m
                     WHERE m.session_id = s.id AND m.role = 'user'
                     ORDER BY m.seq ASC LIMIT 1) AS first_message,
                    (SELECT COUNT(*) FROM chat_messages m WHERE m.session_id = s.id) AS msg_count
                FROM chat_sessions s
                ORDER BY s.last_active_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            {
                "id":             r["id"],
                "created_at":     r["created_at"],
                "last_active_at": r["last_active_at"],
                "title":          (r["first_message"] or "New chat")[:80],
                "msg_count":      r["msg_count"],
            }
            for r in rows
        ]

    def clear_session(self, session_id: str) -> None:
        """Delete all messages for *session_id*."""
        with self._write_lock:
            with self._connect() as conn:
                conn.execute("DELETE FROM chat_messages WHERE session_id = ?", (session_id,))
                conn.execute("DELETE FROM chat_sessions WHERE id = ?", (session_id,))
