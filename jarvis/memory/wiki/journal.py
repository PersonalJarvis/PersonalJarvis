"""Durable candidate-fact journal — the Stage-1/Stage-2 hand-off queue.

The :class:`ConversationFactExtractor` (Stage 1, cheap model, ADD-only)
appends 0..N atomic candidate facts per conversation turn; the
:class:`Consolidator` (Stage 2, body-aware judge) drains them in batches
and marks each one ``consolidated`` / ``rejected`` / ``skipped`` with the
decision it took. The queue lives in the shared ``data/jarvis.db`` SQLite
file (cross-platform, no new dependency — CLOUD.md doctrine) and survives
restarts, so a crash between extraction and consolidation never loses a
fact.

Schema source of truth: ``jarvis/memory/migrations/0005_wiki_candidate_journal.sql``
(applied by ``run_migrations`` for RecallStore-opened databases, and executed
idempotently here for standalone opens — same pattern as ``fts_index``).
Vocabulary source of truth: ``jarvis/memory/wiki/constants.py``.

Concurrency: synchronous sqlite3 + a ``threading.Lock``. All callers are
background tasks (AP-9 — never the voice critical path), and every
operation is a sub-millisecond indexed statement.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .constants import CandidateStatus, CuratorDecision

log = logging.getLogger(__name__)

_MIGRATION_FILE = (
    Path(__file__).resolve().parents[1] / "migrations" / "0005_wiki_candidate_journal.sql"
)


@dataclass(frozen=True, slots=True)
class CandidateFact:
    """One atomic fact proposed by the Stage-1 extractor."""

    fact: str
    kind: str = "other"
    subjects: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class JournalRow:
    """One journal entry as read back for the consolidator."""

    id: int
    created_ms: int
    source_label: str
    turn_hash: str
    fact: str
    kind: str
    subjects: tuple[str, ...]
    status: CandidateStatus


class CandidateJournal:
    """Append/drain/mark interface over ``wiki_candidate_journal``."""

    def __init__(self, db_path: Path, *, clock=time.time) -> None:
        self._db_path = Path(db_path)
        self._clock = clock
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        self._closed = False

    # ------------------------------------------------------------------
    # connection / schema
    # ------------------------------------------------------------------

    def _connection(self) -> sqlite3.Connection | None:
        """Lazy connection. ``None`` once :meth:`close` ran.

        ``close()`` is terminal: an in-flight background task that lands
        after shutdown must NOT silently re-open the database — its
        operation degrades to a logged no-op instead (one lost candidate
        on teardown beats a connection leak on a closing process).
        Must be called with ``self._lock`` held.
        """
        if self._closed:
            return None
        if self._conn is None:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
            # Idempotent DDL (CREATE ... IF NOT EXISTS) straight from the
            # migration file so the journal works even when the DB was never
            # opened through RecallStore/run_migrations.
            conn.executescript(_MIGRATION_FILE.read_text(encoding="utf-8"))
            conn.commit()
            self._conn = conn
        return self._conn

    def close(self) -> None:
        """Close the connection. Terminal — later calls become no-ops."""
        with self._lock:
            self._closed = True
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception:  # noqa: BLE001
                    log.debug("CandidateJournal: close() failed; already closed?")
                self._conn = None

    # ------------------------------------------------------------------
    # write side (Stage 1)
    # ------------------------------------------------------------------

    def append(
        self,
        facts: Sequence[CandidateFact],
        *,
        source_label: str,
        turn_hash: str,
    ) -> int:
        """Append candidate facts as ``pending`` rows. Returns the count."""
        if not facts:
            return 0
        created_ms = int(self._clock() * 1000)
        rows = [
            (
                created_ms,
                source_label,
                turn_hash,
                f.fact,
                f.kind or "other",
                json.dumps(list(f.subjects)),
            )
            for f in facts
            if (f.fact or "").strip()
        ]
        if not rows:
            return 0
        with self._lock:
            conn = self._connection()
            if conn is None:
                log.warning(
                    "CandidateJournal: append after close — %d candidate(s) "
                    "dropped (process is shutting down)", len(rows),
                )
                return 0
            conn.executemany(
                "INSERT INTO wiki_candidate_journal "
                "(created_ms, source_label, turn_hash, fact, kind, subjects) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                rows,
            )
            conn.commit()
        return len(rows)

    # ------------------------------------------------------------------
    # read/drain side (Stage 2)
    # ------------------------------------------------------------------

    def pending(self, limit: int = 20) -> list[JournalRow]:
        """Oldest ``pending`` rows, FIFO, up to ``limit``."""
        with self._lock:
            conn = self._connection()
            if conn is None:
                return []
            cur = conn.execute(
                "SELECT id, created_ms, source_label, turn_hash, fact, kind, "
                "subjects, status FROM wiki_candidate_journal "
                "WHERE status = 'pending' ORDER BY id ASC LIMIT ?",
                (int(limit),),
            )
            out: list[JournalRow] = []
            for row in cur.fetchall():
                try:
                    subjects = tuple(json.loads(row[6]) or ())
                except (TypeError, ValueError):
                    subjects = ()
                out.append(
                    JournalRow(
                        id=row[0],
                        created_ms=row[1],
                        source_label=row[2],
                        turn_hash=row[3],
                        fact=row[4],
                        kind=row[5],
                        subjects=subjects,
                        status=row[7],
                    )
                )
            return out

    def mark(
        self,
        ids: Sequence[int],
        *,
        status: CandidateStatus,
        decision: CuratorDecision | None = None,
        target_path: str | None = None,
    ) -> None:
        """Move rows out of ``pending`` with the judge's verdict."""
        if not ids:
            return
        consolidated_ms = int(self._clock() * 1000)
        with self._lock:
            conn = self._connection()
            if conn is None:
                log.warning("CandidateJournal: mark after close — %d id(s) lost", len(ids))
                return
            conn.executemany(
                "UPDATE wiki_candidate_journal "
                "SET status = ?, decision = ?, target_path = ?, consolidated_ms = ? "
                "WHERE id = ?",
                [(status, decision, target_path, consolidated_ms, int(i)) for i in ids],
            )
            conn.commit()

    # ------------------------------------------------------------------
    # queries
    # ------------------------------------------------------------------

    def backlog_count(self) -> int:
        """Number of ``pending`` rows (drives the journal-pressure trigger)."""
        with self._lock:
            conn = self._connection()
            if conn is None:
                return 0
            row = conn.execute(
                "SELECT COUNT(*) FROM wiki_candidate_journal WHERE status = 'pending'"
            ).fetchone()
            return int(row[0])

    def seen_turn(self, turn_hash: str) -> bool:
        """True when a turn with this hash was already journaled (dedupe)."""
        with self._lock:
            conn = self._connection()
            if conn is None:
                return False
            row = conn.execute(
                "SELECT 1 FROM wiki_candidate_journal WHERE turn_hash = ? LIMIT 1",
                (turn_hash,),
            ).fetchone()
            return row is not None


__all__ = ["CandidateFact", "CandidateJournal", "JournalRow"]
