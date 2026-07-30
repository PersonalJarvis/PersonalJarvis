"""Jarvis-conversations connector: read the shared message store as a source.

Reads the existing ``data/jarvis.db`` ``messages`` table (see
``jarvis/memory/schema.sql``) strictly READ-ONLY through its own
``mode=ro`` SQLite URI connection — this connector never writes to the
shared store and never blocks its tenants.

Chunking: messages are grouped into ONE raw item per (conversation, day):

- conversation id = ``thread_id`` when set, else ``trace_id``;
- day = the UTC calendar date of the message timestamp;
- ``external_id`` = ``"<conversation_id>:<YYYY-MM-DD>"``;
- body = chronological ``role: text`` lines;
- ``thread_key`` = the conversation id;
- ``permalink`` = ``jarvis://chats/<conversation_id>`` (app deep link);
- ``timestamp_utc`` = the LAST message of the chunk.

Cursor / checkpoint contract (documented honestly):

- ``incremental(ctx, cursor)``: the cursor is the highest ``messages``
  rowid seen so far, as a string. Every yielded chunk carries
  ``metadata["max_rowid"]`` so the runtime can advance the cursor to the
  maximum of the yielded values. Incremental re-chunks and re-yields the
  WHOLE day of every (conversation, day) touched by newer rows, so the
  stored chunk always reflects the complete current day (upserts are
  idempotent on ``(source_id, external_id)``). Each reported
  ``max_rowid`` is CAPPED at the highest rowid the touched-day scan itself
  saw: a re-read day can contain rows written after that scan, and reporting
  one of those would advance the cursor past rows belonging to other
  conversations that were never scanned — silently losing them forever.
- ``backfill(ctx, checkpoint)``: chunks stream in deterministic order
  (conversation id, then day). ``checkpoint`` is the ``external_id`` of
  the last persisted chunk; chunks ordering at or before it are skipped
  so an interrupted backfill resumes.
- A missing database file is not an error: backfill/incremental log once
  per path and yield nothing (honest degradation, CLAUDE.md §3).
- ``capabilities.deletes = False``: the message store is append-only from
  this connector's point of view; no tombstones are emitted.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import re
import sqlite3
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jarvis.ultrawiki.types import (
    AuthKind,
    ConnectorCapabilities,
    ConnectorContext,
    IncrementalMode,
    RawItem,
)

log = logging.getLogger(__name__)

_DAY_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_NS_PER_SECOND = 1_000_000_000
_NS_PER_DAY = 86_400 * _NS_PER_SECOND

#: Day-chunks assembled per worker-thread hop. SQLite scans of the shared
#: message store must not run on the event loop (it also serves voice and
#: chat); a small batch amortizes the thread handoff without holding it long.
SCAN_BATCH = 16

# One honest "database missing" log per path per process — not per poll.
_MISSING_DB_LOGGED: set[str] = set()

# The conversation id is thread_id when set, else trace_id — the SQL
# expression COALESCE(NULLIF(thread_id, ''), trace_id) appears verbatim in
# each query below (plain literals, only ? placeholders carry values).

_BACKFILL_QUERY = (
    "SELECT id, COALESCE(NULLIF(thread_id, ''), trace_id) AS conversation_id, "
    "timestamp_ns, role, text FROM messages "
    "ORDER BY conversation_id, timestamp_ns, id"
)

_TOUCHED_QUERY = (
    "SELECT id, COALESCE(NULLIF(thread_id, ''), trace_id) AS conversation_id, "
    "timestamp_ns FROM messages WHERE id > ?"
)

_DAY_QUERY = (
    "SELECT id, COALESCE(NULLIF(thread_id, ''), trace_id) AS conversation_id, "
    "timestamp_ns, role, text FROM messages "
    "WHERE COALESCE(NULLIF(thread_id, ''), trace_id) = ? "
    "AND timestamp_ns >= ? AND timestamp_ns < ? "
    "ORDER BY timestamp_ns, id"
)

# Row shape: (id, conversation_id, timestamp_ns, role, text)
_Row = tuple[int, str, int, str, str]


def _day_utc(timestamp_ns: int) -> str:
    return datetime.fromtimestamp(timestamp_ns // _NS_PER_SECOND, tz=UTC).date().isoformat()


def _day_bounds_ns(day: str) -> tuple[int, int]:
    start = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=UTC)
    start_ns = int(start.timestamp()) * _NS_PER_SECOND
    return start_ns, start_ns + _NS_PER_DAY


def _iso_utc_from_ns(timestamp_ns: int) -> str:
    return datetime.fromtimestamp(timestamp_ns // _NS_PER_SECOND, tz=UTC).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _chunk_item(
    conversation_id: str,
    day: str,
    rows: list[_Row],
    *,
    max_rowid_cap: int | None = None,
) -> RawItem:
    max_rowid = max(row[0] for row in rows)
    if max_rowid_cap is not None:
        # Never report a rowid beyond what the caller's scan actually covered
        # (see the incremental cursor contract in the module docstring).
        max_rowid = min(max_rowid, max_rowid_cap)
    lines = [f"{role}: {text}" for (_rowid, _conv, _ts, role, text) in rows if text]
    return RawItem(
        external_id=f"{conversation_id}:{day}",
        body="\n".join(lines),
        permalink=f"jarvis://chats/{conversation_id}",
        timestamp_utc=_iso_utc_from_ns(rows[-1][2]),
        title=f"Conversation on {day}",
        thread_key=conversation_id,
        metadata={
            "max_rowid": max_rowid,
            "message_count": len(rows),
        },
    )


def _checkpoint_key(checkpoint: str | None) -> tuple[str, str] | None:
    """Split ``"<conversation_id>:<YYYY-MM-DD>"`` into its ordering key."""
    if not checkpoint:
        return None
    conversation_id, sep, day = checkpoint.rpartition(":")
    if not sep or not _DAY_RE.fullmatch(day):
        log.warning(
            "jarvis-conversations: unusable backfill checkpoint %r; "
            "restarting from the beginning (idempotent upserts make this safe)",
            checkpoint,
        )
        return None
    return (conversation_id, day)


def _parse_rowid_cursor(cursor: str | None) -> int:
    if cursor is None or cursor == "":
        return 0
    try:
        return int(cursor)
    except ValueError:
        log.warning(
            "jarvis-conversations: unusable incremental cursor %r; "
            "re-yielding every touched day (idempotent upserts make this safe)",
            cursor,
        )
        return 0


class JarvisConversationsConnector:
    """Stream the shared conversation store as per-day thread chunks."""

    id = "jarvis-conversations"
    label = "Jarvis Conversations"
    auth = AuthKind.NONE
    capabilities = ConnectorCapabilities(
        backfill=True,
        incremental=IncrementalMode.CURSOR,
        deletes=False,
        refresh_interval_s=60.0,
        reconcile_interval_s=86_400.0,
    )

    # ------------------------------------------------------------------
    # Protocol surface
    # ------------------------------------------------------------------

    async def backfill(
        self, ctx: ConnectorContext, checkpoint: str | None = None
    ) -> AsyncIterator[RawItem]:
        conn = await asyncio.to_thread(
            self._open_ro, self._resolve_db_path(ctx)
        )
        if conn is None:
            return
        try:
            resume_after = _checkpoint_key(checkpoint)
            groups = _iter_day_chunks(conn)
            while True:
                # The generator is resumed inside a worker thread, one batch at
                # a time — never from two threads at once, which is what makes
                # the shared connection safe (see _open_ro).
                batch = await asyncio.to_thread(_take, groups, SCAN_BATCH)
                if not batch:
                    break
                for conversation_id, day, rows in batch:
                    if (
                        resume_after is not None
                        and (conversation_id, day) <= resume_after
                    ):
                        continue
                    yield _chunk_item(conversation_id, day, rows)
        finally:
            await asyncio.to_thread(conn.close)

    async def incremental(
        self, ctx: ConnectorContext, cursor: str | None = None
    ) -> AsyncIterator[RawItem]:
        conn = await asyncio.to_thread(
            self._open_ro, self._resolve_db_path(ctx)
        )
        if conn is None:
            return
        try:
            min_rowid = _parse_rowid_cursor(cursor)
            touched, scanned_max_rowid = await asyncio.to_thread(
                _touched_days, conn, min_rowid
            )
            for batch in _batched(touched, SCAN_BATCH):
                days = await asyncio.to_thread(_rows_for_days, conn, batch)
                for conversation_id, day, rows in days:
                    yield _chunk_item(
                        conversation_id,
                        day,
                        rows,
                        max_rowid_cap=scanned_max_rowid,
                    )
        finally:
            await asyncio.to_thread(conn.close)

    # ------------------------------------------------------------------
    # Sync helpers (blocking I/O stays out of the async frames)
    # ------------------------------------------------------------------

    def _resolve_db_path(self, ctx: ConnectorContext) -> Path:
        raw: Any = ctx.config.get("db_path")
        if raw:
            return Path(str(raw))
        # Same canonical resolution every wiki consumer uses (lazy import
        # keeps this package boot-cheap, AP-26).
        from jarvis.memory.wiki.db_path import resolve_wiki_db_path

        return resolve_wiki_db_path(ctx.config.get("data_dir"))

    def _open_ro(self, db_path: Path) -> sqlite3.Connection | None:
        if not db_path.is_file():
            key = str(db_path)
            if key not in _MISSING_DB_LOGGED:
                _MISSING_DB_LOGGED.add(key)
                log.info(
                    "%s: conversation store %s does not exist yet; yielding nothing",
                    self.id,
                    db_path,
                )
            return None
        uri = f"{db_path.resolve().as_uri()}?mode=ro"
        try:
            # check_same_thread=False because every use of this read-only
            # connection is dispatched through asyncio.to_thread, and the
            # thread pool hands out whichever worker is free. Access stays
            # strictly serial (one awaited hop at a time), so no lock is
            # needed — but the connection does move between threads.
            return sqlite3.connect(uri, uri=True, check_same_thread=False)
        except sqlite3.Error as exc:
            log.warning(
                "%s: could not open conversation store %s read-only: %s",
                self.id,
                db_path,
                exc,
            )
            return None


def _take(groups: Iterator[tuple[str, str, list[_Row]]], size: int) -> list[
    tuple[str, str, list[_Row]]
]:
    """Pull up to *size* day-chunks off a running group iterator (blocking)."""
    return list(itertools.islice(groups, size))


def _batched(values: list[tuple[str, str]], size: int) -> list[list[tuple[str, str]]]:
    return [values[start : start + size] for start in range(0, len(values), size)]


def _iter_day_chunks(
    conn: sqlite3.Connection,
) -> Iterator[tuple[str, str, list[_Row]]]:
    """Stream (conversation_id, day, rows) groups in deterministic order."""
    current_key: tuple[str, str] | None = None
    rows: list[_Row] = []
    for row in conn.execute(_BACKFILL_QUERY):
        typed: _Row = (int(row[0]), str(row[1]), int(row[2]), str(row[3]), str(row[4]))
        key = (typed[1], _day_utc(typed[2]))
        if key != current_key:
            if current_key is not None and rows:
                yield current_key[0], current_key[1], rows
            current_key = key
            rows = []
        rows.append(typed)
    if current_key is not None and rows:
        yield current_key[0], current_key[1], rows


def _touched_days(
    conn: sqlite3.Connection, min_rowid: int
) -> tuple[list[tuple[str, str]], int]:
    """``(pairs, scanned_max_rowid)`` for rows newer than ``min_rowid``.

    ``scanned_max_rowid`` is the highest rowid this scan actually saw — the
    high-water mark the caller may safely report as the new cursor. It is 0
    when nothing is newer than ``min_rowid``.
    """
    touched: set[tuple[str, str]] = set()
    scanned_max_rowid = 0
    for rowid, conversation_id, timestamp_ns in conn.execute(
        _TOUCHED_QUERY, (min_rowid,)
    ):
        touched.add((str(conversation_id), _day_utc(int(timestamp_ns))))
        scanned_max_rowid = max(scanned_max_rowid, int(rowid))
    return sorted(touched), scanned_max_rowid


def _rows_for_day(conn: sqlite3.Connection, conversation_id: str, day: str) -> list[_Row]:
    start_ns, end_ns = _day_bounds_ns(day)
    return [
        (int(row[0]), str(row[1]), int(row[2]), str(row[3]), str(row[4]))
        for row in conn.execute(_DAY_QUERY, (conversation_id, start_ns, end_ns))
    ]


def _rows_for_days(
    conn: sqlite3.Connection, pairs: list[tuple[str, str]]
) -> list[tuple[str, str, list[_Row]]]:
    """Re-read a batch of whole days in one worker-thread hop (blocking)."""
    result: list[tuple[str, str, list[_Row]]] = []
    for conversation_id, day in pairs:
        rows = _rows_for_day(conn, conversation_id, day)
        if rows:
            result.append((conversation_id, day, rows))
    return result


__all__ = ["JarvisConversationsConnector"]
