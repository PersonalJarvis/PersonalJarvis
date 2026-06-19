"""Recall memory: SQLite-FTS5 conversation log with BM25 ranking.

Implements the `MemoryStore` protocol but additionally provides:
- `record_message(...)` for the conversation log (used by `MessageRecorder`)
- `search_messages(query, k)` with BM25 ranking over text/tool_calls/reasoning
- `prune_older_than(days)` for retention policy

Thread-safe via aiosqlite. WAL mode is enabled. One process = one
connection; concurrent writers from other processes are not the goal.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import aiosqlite

from .migration_runner import run_migrations

SCHEMA_FILE = Path(__file__).parent / "schema.sql"


def _sanitize_fts5_query(query: str) -> str:
    """Wrap query as FTS5 phrase to neutralize special chars."""
    cleaned = query.replace('"', " ").strip()
    return f'"{cleaned}"' if cleaned else ""


def _escape_like(q: str) -> str:
    """Escape backslash, percent, underscore for SQL LIKE with ESCAPE '\\'."""
    return q.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")


class RecallStore:
    """Message log + KV store on a shared SQLite database.

    BM25 weights (10, 5, 8) prioritise: text (most important) > reasoning > tool_calls.
    Smaller BM25 rank = better match.
    """

    name: str = "sqlite-recall"

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: aiosqlite.Connection | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def open(self) -> None:
        """Opens the DB connection and runs the schema bootstrap.

        Two-phase bootstrap:
        1. ``executescript(schema.sql)`` — idempotent ``CREATE IF NOT
           EXISTS`` statements. Fresh databases get tables with the
           current shape; existing databases keep what they have.
        2. :func:`run_migrations` — applies any forward-migration
           SQL files under ``jarvis/memory/migrations/`` that have
           not been applied yet (tracked via ``PRAGMA user_version``).
           Phase-2 is the only path that can widen a ``CHECK``
           constraint on a pre-existing table because
           ``CREATE IF NOT EXISTS`` cannot.
        """
        if self._conn is not None:
            return
        self._conn = await aiosqlite.connect(self._db_path, isolation_level=None)
        self._conn.row_factory = aiosqlite.Row
        schema = SCHEMA_FILE.read_text(encoding="utf-8")
        await self._conn.executescript(schema)
        await run_migrations(self._conn)

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def __aenter__(self) -> RecallStore:
        await self.open()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    async def _ensure_open(self) -> aiosqlite.Connection:
        """Lazy-opens if necessary; returns the connection.

        Allows the store instance to be constructed synchronously
        (e.g. in sync factories) with async initialisation happening on the
        first actual access — on the correct event loop.
        """
        if self._conn is None:
            await self.open()
        assert self._conn is not None
        return self._conn

    def _require_conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("RecallStore ist nicht geöffnet — rufe open() oder nutze 'async with'.")
        return self._conn

    # ------------------------------------------------------------------
    # Message-Log
    # ------------------------------------------------------------------

    async def record_message(
        self,
        *,
        trace_id: str,
        role: str,
        text: str,
        thread_id: str | None = None,
        timestamp_ns: int | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
        reasoning: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        tokens_in: int = 0,
        tokens_out: int = 0,
    ) -> int:
        """Writes a message to the log and returns the SQLite rowid."""
        import time
        conn = await self._ensure_open()
        ts = timestamp_ns if timestamp_ns is not None else time.time_ns()
        tc_json = json.dumps(tool_calls, ensure_ascii=False) if tool_calls else None
        cur = await conn.execute(
            """
            INSERT INTO messages (trace_id, thread_id, timestamp_ns, role, text,
                                  tool_calls, reasoning, provider, model,
                                  tokens_in, tokens_out)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (trace_id, thread_id, ts, role, text, tc_json, reasoning, provider, model,
             tokens_in, tokens_out),
        )
        rowid = cur.lastrowid
        await cur.close()
        return int(rowid or 0)

    async def search_messages(
        self,
        query: str,
        k: int = 5,
        role: str | None = None,
        thread_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Full-text search via FTS5 BM25. Returns messages with rank score.

        BM25 weights: text=10, tool_calls=5, reasoning=8 (see plan §20.2).
        """
        safe_query = _sanitize_fts5_query(query)
        if not safe_query:
            return []
        conn = await self._ensure_open()
        sql = """
            SELECT m.id, m.trace_id, m.thread_id, m.timestamp_ns, m.role, m.text,
                   m.tool_calls, m.reasoning, m.provider, m.model,
                   bm25(messages_fts, 10, 5, 8) AS rank
            FROM messages_fts
            JOIN messages m ON m.id = messages_fts.rowid
            WHERE messages_fts MATCH ?
        """
        params: list[Any] = [safe_query]
        if role:
            sql += " AND m.role = ?"
            params.append(role)
        if thread_id:
            sql += " AND m.thread_id = ?"
            params.append(thread_id)
        sql += " ORDER BY rank ASC LIMIT ?"
        params.append(k)

        cur = await conn.execute(sql, tuple(params))
        rows = await cur.fetchall()
        await cur.close()
        return [dict(r) for r in rows]

    async def recent_messages(
        self,
        limit: int = 20,
        role: str | None = None,
        thread_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Returns the N most recent messages (without full-text search)."""
        conn = await self._ensure_open()
        sql = "SELECT * FROM messages"
        params: list[Any] = []
        where: list[str] = []
        if role:
            where.append("role = ?")
            params.append(role)
        if thread_id:
            where.append("thread_id = ?")
            params.append(thread_id)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY timestamp_ns DESC LIMIT ?"
        params.append(limit)
        cur = await conn.execute(sql, tuple(params))
        rows = await cur.fetchall()
        await cur.close()
        return [dict(r) for r in rows]

    async def prune_older_than(self, days: int) -> int:
        """Deletes messages older than N days. Returns the number of deleted rows."""
        conn = await self._ensure_open()
        cur = await conn.execute(
            "DELETE FROM messages WHERE datetime(created_at) < datetime('now', ?)",
            (f"-{days} days",),
        )
        rowcount = cur.rowcount
        await cur.close()
        return rowcount

    # ------------------------------------------------------------------
    # MemoryStore-Protocol (KV-Store über `kv_store`-Tabelle)
    # ------------------------------------------------------------------

    async def put(self, namespace: str, key: str, value: dict[str, Any]) -> None:
        conn = await self._ensure_open()
        await conn.execute(
            """
            INSERT INTO kv_store (namespace, key, value_json, updated_at)
            VALUES (?, ?, ?, datetime('now'))
            ON CONFLICT(namespace, key) DO UPDATE SET
                value_json = excluded.value_json,
                updated_at = excluded.updated_at
            """,
            (namespace, key, json.dumps(value, ensure_ascii=False)),
        )

    async def get(self, namespace: str, key: str) -> dict[str, Any] | None:
        conn = await self._ensure_open()
        cur = await conn.execute(
            "SELECT value_json FROM kv_store WHERE namespace = ? AND key = ?",
            (namespace, key),
        )
        row = await cur.fetchone()
        await cur.close()
        if row is None:
            return None
        return json.loads(row["value_json"])

    async def search(
        self,
        namespace: str,
        query: str,
        k: int = 5,
    ) -> list[tuple[str, dict[str, Any], float]]:
        """FTS search in the "messages" namespace → message log.

        For other namespaces this falls back to a naive LIKE query.
        """
        if namespace == "messages":
            rows = await self.search_messages(query, k=k)
            return [(str(r["id"]), r, float(r["rank"])) for r in rows]

        conn = await self._ensure_open()
        cur = await conn.execute(
            """
            SELECT key, value_json FROM kv_store
            WHERE namespace = ? AND value_json LIKE ? ESCAPE '\\'
            LIMIT ?
            """,
            (namespace, f"%{_escape_like(query)}%", k),
        )
        rows = await cur.fetchall()
        await cur.close()
        return [(r["key"], json.loads(r["value_json"]), 0.0) for r in rows]

    async def forget(self, namespace: str, key: str) -> None:
        conn = await self._ensure_open()
        await conn.execute(
            "DELETE FROM kv_store WHERE namespace = ? AND key = ?",
            (namespace, key),
        )

    # ------------------------------------------------------------------
    # Awareness L2 — Story Tracker (Phase A2, Plan §6)
    # ------------------------------------------------------------------

    async def record_frame(
        self,
        *,
        window_title: str,
        process_name: str,
        timestamp_ns: int,
        salience_score: int,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        """Persists an awareness frame and returns the SQLite rowid.

        Frames are raw snapshots from `WindowFocusWatcher` (Phase A1),
        filtered through the `SalienceScorer`. They are later used for L3
        recall. The `metadata` dict is stored JSON-encoded (git_branch,
        open_file_hint, etc.) — None is persisted as SQL NULL.
        """
        conn = await self._ensure_open()
        meta_json = json.dumps(metadata, ensure_ascii=False) if metadata else None
        cur = await conn.execute(
            """
            INSERT INTO awareness_frames (timestamp_ns, window_title, process_name,
                                          salience_score, metadata_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (timestamp_ns, window_title, process_name, salience_score, meta_json),
        )
        rowid = cur.lastrowid
        await cur.close()
        return int(rowid or 0)

    async def record_episode(
        self,
        *,
        started_at_ns: int,
        ended_at_ns: int,
        trigger_kind: str,
        summary: str,
        frame_count: int,
        primary_app: str,
        tokens_in: int = 0,
        tokens_out: int = 0,
    ) -> int:
        """Persists an episode generated by the Verdichter and returns the rowid.

        Called by `StoryTracker._flush_to_verdichter()` after each trigger
        (window switch, idle, hard-cap timer). The AFTER-INSERT trigger
        `awareness_episodes_ai` automatically populates the FTS5 index —
        no extra step required.
        """
        conn = await self._ensure_open()
        cur = await conn.execute(
            """
            INSERT INTO awareness_episodes (started_at_ns, ended_at_ns, trigger_kind,
                                            summary, frame_count, primary_app,
                                            tokens_in, tokens_out)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (started_at_ns, ended_at_ns, trigger_kind, summary, frame_count,
             primary_app, tokens_in, tokens_out),
        )
        rowid = cur.lastrowid
        await cur.close()
        return int(rowid or 0)

    async def recent_episodes(
        self,
        *,
        limit: int = 10,
        since_ns: int | None = None,
    ) -> list[dict[str, Any]]:
        """Returns the N most recent episodes, sorted by started_at_ns DESC.

        `since_ns` optionally filters for episodes that started *at* or *after*
        a given timestamp (inclusive). None means no filter.
        """
        conn = await self._ensure_open()
        sql = "SELECT * FROM awareness_episodes"
        params: list[Any] = []
        if since_ns is not None:
            sql += " WHERE started_at_ns >= ?"
            params.append(since_ns)
        sql += " ORDER BY started_at_ns DESC LIMIT ?"
        params.append(limit)
        cur = await conn.execute(sql, tuple(params))
        rows = await cur.fetchall()
        await cur.close()
        return [dict(r) for r in rows]

    async def search_episodes(
        self,
        *,
        query: str,
        limit: int = 10,
        since_ns: int | None = None,
    ) -> list[dict[str, Any]]:
        """Full-text search across episode summaries via FTS5 MATCH + BM25 rank.

        Joins ``awareness_episodes_fts`` with ``awareness_episodes`` over the
        rowid and sorts by FTS rank (smaller = better match). Returns the
        full episode records with the rank score as the ``rank`` key.

        ``since_ns`` optionally restricts results to episodes whose
        ``started_at_ns`` is at or after the given timestamp (inclusive).
        ``None`` means no time filter — backward-compatible with the
        original two-argument signature.
        """
        safe_query = _sanitize_fts5_query(query)
        if not safe_query:
            return []
        conn = await self._ensure_open()
        sql = (
            "SELECT e.*, awareness_episodes_fts.rank AS rank "
            "FROM awareness_episodes_fts "
            "JOIN awareness_episodes e ON e.id = awareness_episodes_fts.rowid "
            "WHERE awareness_episodes_fts MATCH ?"
        )
        params: list[Any] = [safe_query]
        if since_ns is not None:
            sql += " AND e.started_at_ns >= ?"
            params.append(since_ns)
        sql += " ORDER BY rank ASC LIMIT ?"
        params.append(limit)
        cur = await conn.execute(sql, tuple(params))
        rows = await cur.fetchall()
        await cur.close()
        return [dict(r) for r in rows]
