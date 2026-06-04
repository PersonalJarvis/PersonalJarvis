"""Durable, segmented chat store backed by sqlite3.

History: this began as an in-memory dict for Phase 1a, with the explicit plan
to swap in SQLite for Phase 2 ("Persistenz (SQLite) kommt in Phase 2 …"). The
Chats conversation manager (2026-05-30) is that swap. The public async API is
unchanged (``create_thread`` / ``ensure_thread`` / ``add_message`` /
``list_threads`` / ``get_thread``) so the launcher + desktop wiring is
untouched; new capabilities (``delete_thread``, ``prune_older_than``,
per-thread ``preview`` / ``title`` / ``updated_at_ns``) are additive.

Storage mirrors ``jarvis/sessions/store.py``: a single ``sqlite3`` connection
in WAL mode, guarded by a ``threading.Lock`` (writes happen from the asyncio
loop, reads from FastAPI route handlers — both on the same in-process loop, but
the lock keeps us safe if a future caller reads from a worker thread). The
``db_path`` defaults to ``:memory:`` so existing callers/tests that construct
``ChatStore(bus=...)`` keep working in-process without touching disk; the boot
path passes ``data/chats.db`` for real persistence.

Ordering is by SQLite ``rowid`` (strictly monotonic per insert), not by
wall-clock — Windows ``time_ns()`` resolution can otherwise tie two fast
appends and make message/thread order non-deterministic.
"""
from __future__ import annotations

import sqlite3
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from time import time_ns
from typing import TYPE_CHECKING, Any

from jarvis.core.events import MessageSent, ThreadCreated

if TYPE_CHECKING:
    from jarvis.core.bus import EventBus


@dataclass(slots=True)
class ChatMessage:
    message_id: str
    thread_id: str
    role: str  # "user" | "assistant" | "system"
    text: str
    timestamp_ns: int


# Titles we treat as auto-derivable: the first user message replaces them.
# An explicitly chosen title is never overwritten.
_PLACEHOLDER_TITLES: frozenset[str] = frozenset({"", "New Thread", "New Chat"})

_TITLE_MAX_CHARS = 80

_SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_threads (
    thread_id      TEXT PRIMARY KEY,
    title          TEXT NOT NULL DEFAULT '',
    kind           TEXT NOT NULL DEFAULT 'text',
    created_at_ns  INTEGER NOT NULL,
    updated_at_ns  INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_messages (
    message_id    TEXT PRIMARY KEY,
    thread_id     TEXT NOT NULL,
    role          TEXT NOT NULL,
    text          TEXT NOT NULL,
    timestamp_ns  INTEGER NOT NULL,
    FOREIGN KEY (thread_id) REFERENCES chat_threads(thread_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_chat_messages_thread
    ON chat_messages(thread_id);
"""


def default_chats_db_path(data_dir: str = "./data") -> Path:
    """Resolve ``chats.db`` to sit beside ``sessions.db``.

    Uses the *identical* formula the session-store bootstrap uses in
    ``server.py`` (``Path(cfg.memory.data_dir).parent / "data" / <name>``), so
    ``chats.db`` is guaranteed to land in the same directory as ``sessions.db``
    regardless of how ``data_dir`` is configured. With the default ``./data``
    that is repo-root ``data/chats.db``.
    """
    return Path(data_dir).parent / "data" / "chats.db"


def _derive_title(text: str) -> str:
    """First user message → a one-line title, truncated."""
    single = " ".join(text.split())
    if len(single) <= _TITLE_MAX_CHARS:
        return single
    return single[: _TITLE_MAX_CHARS - 1].rstrip() + "…"


class ChatStore:
    """sqlite3-backed thread/message store. Sync DB ops under a threading.Lock,
    async public mutators (they also publish on the EventBus)."""

    def __init__(self, *, bus: EventBus, db_path: str | Path = ":memory:") -> None:
        self._bus = bus
        self._db_path = str(db_path)
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()

    # ------- Lifecycle ------------------------------------------------
    def open(self) -> None:
        """Open the connection + create the schema. Idempotent.

        Optional to call explicitly — every method lazy-opens via
        :meth:`_ensure_conn`. The boot path calls it so any error surfaces
        at startup rather than on the first message.
        """
        self._ensure_conn()

    def _ensure_conn(self) -> sqlite3.Connection:
        with self._lock:
            if self._conn is not None:
                return self._conn
            if self._db_path != ":memory:":
                Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(
                self._db_path,
                check_same_thread=False,
                isolation_level=None,  # autocommit; WAL is the lock manager
            )
            if self._db_path != ":memory:":
                conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.row_factory = sqlite3.Row
            conn.executescript(_SCHEMA)
            self._conn = conn
            return conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    # ------- Queries (sync, reads are idempotent) ---------------------
    def list_threads(self) -> list[dict[str, Any]]:
        """All threads, most-recently-active first.

        Recency uses the largest message ``rowid`` per thread (monotonic with
        insertion across all threads), so it is deterministic regardless of
        clock granularity. Empty threads sort last by creation order.
        """
        conn = self._ensure_conn()
        with self._lock:
            rows = conn.execute(
                """
                SELECT
                    t.thread_id      AS thread_id,
                    t.title          AS title,
                    t.kind           AS kind,
                    t.created_at_ns  AS created_at_ns,
                    t.updated_at_ns  AS updated_at_ns,
                    (SELECT COUNT(*) FROM chat_messages m
                        WHERE m.thread_id = t.thread_id) AS message_count,
                    (SELECT m.text FROM chat_messages m
                        WHERE m.thread_id = t.thread_id AND m.role = 'user'
                        ORDER BY m.rowid LIMIT 1) AS preview,
                    (SELECT MAX(m.rowid) FROM chat_messages m
                        WHERE m.thread_id = t.thread_id) AS last_seq
                FROM chat_threads t
                ORDER BY (last_seq IS NULL), last_seq DESC, t.rowid DESC
                """
            ).fetchall()
        return [
            {
                "thread_id": r["thread_id"],
                "title": r["title"],
                "kind": r["kind"],
                "created_at_ns": r["created_at_ns"],
                "updated_at_ns": r["updated_at_ns"],
                "message_count": r["message_count"],
                "preview": r["preview"] or "",
            }
            for r in rows
        ]

    def get_thread(self, thread_id: str) -> dict[str, Any] | None:
        conn = self._ensure_conn()
        with self._lock:
            head = conn.execute(
                "SELECT thread_id, title, kind, created_at_ns, updated_at_ns "
                "FROM chat_threads WHERE thread_id = ?",
                (thread_id,),
            ).fetchone()
            if head is None:
                return None
            msgs = conn.execute(
                "SELECT message_id, thread_id, role, text, timestamp_ns "
                "FROM chat_messages WHERE thread_id = ? ORDER BY rowid",
                (thread_id,),
            ).fetchall()
        return {
            "thread_id": head["thread_id"],
            "title": head["title"],
            "kind": head["kind"],
            "created_at_ns": head["created_at_ns"],
            "updated_at_ns": head["updated_at_ns"],
            "messages": [
                {
                    "message_id": m["message_id"],
                    "thread_id": m["thread_id"],
                    "role": m["role"],
                    "text": m["text"],
                    "timestamp_ns": m["timestamp_ns"],
                }
                for m in msgs
            ],
        }

    # ------- Mutations ------------------------------------------------
    async def create_thread(
        self, *, title: str, thread_id: str | None = None, kind: str = "text"
    ) -> dict[str, Any]:
        tid = thread_id or str(uuid.uuid4())
        conn = self._ensure_conn()
        now = time_ns()
        with self._lock:
            existing = conn.execute(
                "SELECT created_at_ns, title, kind FROM chat_threads WHERE thread_id = ?",
                (tid,),
            ).fetchone()
            if existing is None:
                conn.execute(
                    "INSERT INTO chat_threads "
                    "(thread_id, title, kind, created_at_ns, updated_at_ns) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (tid, title, kind, now, now),
                )
                created_at = now
                final_title = title
            else:
                created_at = existing["created_at_ns"]
                final_title = existing["title"]
        await self._bus.publish(
            ThreadCreated(source_layer="chat", thread_id=tid, title=final_title)
        )
        return {
            "thread_id": tid,
            "title": final_title,
            "created_at_ns": created_at,
            "message_count": 0,
        }

    async def ensure_thread(
        self, thread_id: str, *, title: str = "New Thread", kind: str = "text"
    ) -> None:
        conn = self._ensure_conn()
        with self._lock:
            exists = conn.execute(
                "SELECT 1 FROM chat_threads WHERE thread_id = ?", (thread_id,)
            ).fetchone()
        if exists is not None:
            return
        await self.create_thread(title=title, thread_id=thread_id, kind=kind)

    async def add_message(
        self, *, thread_id: str, role: str, text: str
    ) -> ChatMessage:
        await self.ensure_thread(thread_id)
        msg = ChatMessage(
            message_id=str(uuid.uuid4()),
            thread_id=thread_id,
            role=role,
            text=text,
            timestamp_ns=time_ns(),
        )
        conn = self._ensure_conn()
        with self._lock:
            conn.execute(
                "INSERT INTO chat_messages "
                "(message_id, thread_id, role, text, timestamp_ns) "
                "VALUES (?, ?, ?, ?, ?)",
                (msg.message_id, thread_id, role, text, msg.timestamp_ns),
            )
            conn.execute(
                "UPDATE chat_threads SET updated_at_ns = ? WHERE thread_id = ?",
                (msg.timestamp_ns, thread_id),
            )
            # Auto-title from the first user message, unless a real title was set.
            if role == "user":
                head = conn.execute(
                    "SELECT title FROM chat_threads WHERE thread_id = ?",
                    (thread_id,),
                ).fetchone()
                if head is not None and head["title"] in _PLACEHOLDER_TITLES:
                    conn.execute(
                        "UPDATE chat_threads SET title = ? WHERE thread_id = ?",
                        (_derive_title(text), thread_id),
                    )
        await self._bus.publish(
            MessageSent(
                source_layer="chat",
                thread_id=thread_id,
                role=role,
                text=text,
            )
        )
        return msg

    async def delete_thread(self, thread_id: str) -> bool:
        """Delete a thread and (via ON DELETE CASCADE) its messages."""
        conn = self._ensure_conn()
        with self._lock:
            cur = conn.execute(
                "DELETE FROM chat_threads WHERE thread_id = ?", (thread_id,)
            )
            return cur.rowcount > 0

    # ------- Retention / maintenance ----------------------------------
    def prune_older_than(self, days: int) -> int:
        """Delete threads whose last activity is older than ``days``.

        ``days <= 0`` disables pruning (returns 0), matching SessionStore.
        """
        if days <= 0:
            return 0
        cutoff = time_ns() - int(days) * 86_400 * 1_000_000_000
        conn = self._ensure_conn()
        with self._lock:
            cur = conn.execute(
                "DELETE FROM chat_threads WHERE updated_at_ns < ?", (cutoff,)
            )
            return cur.rowcount

    def _backdate_for_test(self, thread_id: str, *, days: int) -> None:
        """Test helper: move a thread's timestamps into the past."""
        conn = self._ensure_conn()
        past = time_ns() - int(days) * 86_400 * 1_000_000_000
        with self._lock:
            conn.execute(
                "UPDATE chat_threads SET created_at_ns = ?, updated_at_ns = ? "
                "WHERE thread_id = ?",
                (past, past, thread_id),
            )
