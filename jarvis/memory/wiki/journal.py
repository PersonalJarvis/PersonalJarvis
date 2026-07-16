"""Durable candidate-fact journal — the Stage-1/Stage-2 hand-off queue.

The :class:`ConversationFactExtractor` (Stage 1, cheap model, ADD-only)
appends 0..N atomic candidate facts per conversation turn; the
:class:`Consolidator` (Stage 2, body-aware judge) drains them in batches
and marks each one ``consolidated`` / ``rejected`` / ``skipped`` with the
decision it took. The queue lives in the shared ``data/jarvis.db`` SQLite
file (cross-platform, no new dependency — CLOUD.md doctrine) and survives
restarts, so a crash between extraction and consolidation never loses a
fact.

Schema source of truth: migrations ``0005`` through ``0008`` under
``jarvis/memory/migrations`` (applied by ``run_migrations`` for
RecallStore-opened databases, and executed idempotently here for standalone
opens — the same pattern as ``fts_index``).
Vocabulary source of truth: ``jarvis/memory/wiki/constants.py``.

Concurrency: synchronous sqlite3 + a ``threading.Lock``. All callers are
background tasks (AP-9 — never the voice critical path), and every
operation is a sub-millisecond indexed statement.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from jarvis.core.redact import safe_preview

from .constants import CandidateStatus, CuratorDecision
from .secret_guard import contains_secret

log = logging.getLogger(__name__)

_MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"
_MIGRATION_FILES = (
    _MIGRATIONS_DIR / "0005_wiki_candidate_journal.sql",
    _MIGRATIONS_DIR / "0006_wiki_extraction_audit.sql",
    _MIGRATIONS_DIR / "0007_wiki_candidate_capture.sql",
    _MIGRATIONS_DIR / "0008_wiki_candidate_evidence_excerpt.sql",
)

CaptureStatus = Literal["started", "filtered", "empty", "candidates", "failed"]
_CAPTURE_STATUSES = frozenset({"started", "filtered", "empty", "candidates", "failed"})
_CAPTURE_TERMINAL_STATUSES = frozenset({"filtered", "empty", "candidates"})
_CAPTURE_FINISH_STATUSES = frozenset({"filtered", "empty", "candidates", "failed"})
_CAPTURE_STALE_AFTER_MS = 5 * 60 * 1000
_MAX_FACT_CHARS = 2_000
_MAX_EVIDENCE_CHARS = 1_200
_MAX_SUBJECTS = 12
_SAFE_SUBJECT_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,78}[a-z0-9])?\Z")

_SAFE_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9_.:/-]{1,240}\Z")
_SAFE_HASH_RE = re.compile(r"[0-9A-Fa-f]{32,128}\Z")
_SAFE_TOKEN_RE = re.compile(r"[A-Za-z0-9_.:/-]{1,80}\Z")
_KNOWN_ERROR_CODES = frozenset(
    {
        "below-min-chars",
        "cancelled",
        "extractor-disabled",
        "invalid-structured-output",
        "journal_closed",
        "journal_error",
        "journal-write-failed",
        "malformed_json",
        "no-user-turns",
        "other",
        "provider-chain-failed",
        "provider_error",
        "provider_timeout",
        "provider_unavailable",
        "timeout",
        "truncated",
        "unexpected",
        "unknown",
    }
)


@dataclass(frozen=True, slots=True)
class CandidateFact:
    """One atomic fact proposed by the Stage-1 extractor."""

    fact: str
    kind: str = "other"
    subjects: tuple[str, ...] = ()
    evidence_turn_id: str = ""
    evidence_excerpt: str = ""


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
    evidence_turn_id: str
    evidence_excerpt: str
    session_id: str
    review_key: str
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
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA foreign_keys=ON")
            # Idempotent DDL (CREATE ... IF NOT EXISTS) straight from the
            # migration files so the journal works even when the DB was never
            # opened through RecallStore/run_migrations. Do not bump
            # ``user_version`` here: a standalone journal may precede the base
            # RecallStore schema and must not make its earlier migrations look
            # applied.
            for migration in _MIGRATION_FILES:
                conn.executescript(migration.read_text(encoding="utf-8"))
            # Development builds briefly stored evidence_excerpt as a column on
            # wiki_candidate_evidence. Copy those values into the idempotent
            # one-to-one table without dropping or rewriting the legacy column.
            evidence_columns = {
                str(row[1])
                for row in conn.execute(
                    "PRAGMA table_info(wiki_candidate_evidence)"
                ).fetchall()
            }
            if "evidence_excerpt" in evidence_columns:
                conn.execute(
                    "INSERT OR IGNORE INTO wiki_candidate_evidence_excerpt "
                    "(candidate_id, evidence_excerpt) "
                    "SELECT candidate_id, evidence_excerpt "
                    "FROM wiki_candidate_evidence "
                    "WHERE evidence_excerpt <> ''"
                )
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
        rows = _normalise_candidates(facts)
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
            try:
                self._insert_candidates(
                    conn,
                    rows,
                    created_ms=created_ms,
                    source_label=source_label,
                    turn_hash=turn_hash,
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return len(rows)

    def commit_capture_candidates(
        self,
        facts: Sequence[CandidateFact],
        *,
        review_key: str,
        source_label: str,
        turn_hash: str,
        provider: str = "",
        duration_ms: int = 0,
    ) -> int:
        """Atomically append candidates and finish their extraction audit.

        The audit row must already be claimed as ``started``. Any insertion or
        audit-update failure rolls the entire transaction back, leaving no
        orphan candidate that a stale retry could append a second time.
        """
        key = _safe_identifier(review_key, allow_empty=False)
        rows = _normalise_candidates(facts)
        now_ms = self._now_ms()
        with self._lock:
            conn = self._connection()
            if conn is None:
                return 0
            try:
                if rows:
                    self._insert_candidates(
                        conn,
                        rows,
                        created_ms=now_ms,
                        source_label=source_label,
                        turn_hash=turn_hash,
                        review_key=key,
                    )
                status = "candidates" if rows else "empty"
                cur = conn.execute(
                    "UPDATE wiki_extraction_audit SET status = ?, candidate_count = ?, "
                    "provider = ?, duration_ms = ?, error_code = '', updated_ms = ?, "
                    "finished_ms = ? WHERE review_key = ? AND status = 'started'",
                    (
                        status,
                        len(rows),
                        _safe_token(provider, fallback="", allow_empty=True),
                        max(0, int(duration_ms)),
                        now_ms,
                        now_ms,
                        key,
                    ),
                )
                if cur.rowcount != 1:
                    raise RuntimeError("capture audit is not claimable")
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return len(rows)

    @staticmethod
    def _insert_candidates(
        conn: sqlite3.Connection,
        rows: Sequence[CandidateFact],
        *,
        created_ms: int,
        source_label: str,
        turn_hash: str,
        review_key: str | None = None,
    ) -> None:
        """Insert already-normalised rows into the current transaction."""
        for fact in rows:
            cur = conn.execute(
                "INSERT INTO wiki_candidate_journal "
                "(created_ms, source_label, turn_hash, fact, kind, subjects) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    created_ms,
                    _safe_label(source_label),
                    _safe_identifier(turn_hash, allow_empty=True),
                    fact.fact,
                    _safe_token(fact.kind, fallback="other"),
                    json.dumps(list(fact.subjects)),
                ),
            )
            candidate_id = int(cur.lastrowid)
            conn.execute(
                "INSERT INTO wiki_candidate_evidence "
                "(candidate_id, evidence_turn_id) VALUES (?, ?)",
                (
                    candidate_id,
                    _safe_identifier(fact.evidence_turn_id, allow_empty=True),
                ),
            )
            conn.execute(
                "INSERT INTO wiki_candidate_evidence_excerpt "
                "(candidate_id, evidence_excerpt) VALUES (?, ?)",
                (
                    candidate_id,
                    safe_preview(
                        fact.evidence_excerpt,
                        max_chars=_MAX_EVIDENCE_CHARS,
                    ),
                ),
            )
            if review_key is not None:
                conn.execute(
                    "INSERT INTO wiki_candidate_capture (candidate_id, review_key) "
                    "VALUES (?, ?)",
                    (candidate_id, review_key),
                )

    # ------------------------------------------------------------------
    # read/drain side (Stage 2)
    # ------------------------------------------------------------------

    def pending(
        self,
        limit: int = 20,
        *,
        review_keys: Sequence[str] | None = None,
    ) -> list[JournalRow]:
        """Oldest pending rows, optionally scoped to exact capture reviews."""
        keys: tuple[str, ...] | None = None
        if review_keys is not None:
            keys = tuple(
                dict.fromkeys(
                    _safe_identifier(key, allow_empty=False)
                    for key in review_keys
                    if key
                )
            )
            if not keys:
                return []
        with self._lock:
            conn = self._connection()
            if conn is None:
                return []
            select = (
                "SELECT j.id, j.created_ms, j.source_label, j.turn_hash, j.fact, "
                "j.kind, j.subjects, COALESCE(e.evidence_turn_id, ''), "
                "COALESCE(x.evidence_excerpt, ''), "
                "COALESCE(a.session_id, ''), COALESCE(c.review_key, ''), "
                "j.status "
                "FROM wiki_candidate_journal AS j "
                "LEFT JOIN wiki_candidate_evidence AS e ON e.candidate_id = j.id "
                "LEFT JOIN wiki_candidate_evidence_excerpt AS x "
                "ON x.candidate_id = j.id "
                "LEFT JOIN wiki_candidate_capture AS c ON c.candidate_id = j.id "
                "LEFT JOIN wiki_extraction_audit AS a ON a.review_key = c.review_key "
            )
            if keys is None:
                cur = conn.execute(
                    select
                    + "WHERE j.status = 'pending' ORDER BY j.id ASC LIMIT ?",
                    (int(limit),),
                )
            else:
                placeholders = ",".join("?" for _ in keys)
                cur = conn.execute(
                    select
                    + "WHERE j.status = 'pending' "
                    + f"AND c.review_key IN ({placeholders}) "  # noqa: S608
                    + "ORDER BY j.id ASC LIMIT ?",
                    (*keys, int(limit)),
                )
            out: list[JournalRow] = []
            for row in cur.fetchall():
                try:
                    subjects = normalise_subjects(json.loads(row[6]) or ())
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
                        evidence_turn_id=row[7],
                        evidence_excerpt=row[8],
                        session_id=row[9],
                        review_key=row[10],
                        status=row[11],
                    )
                )
            return out

    # ------------------------------------------------------------------
    # durable extraction-capture audit
    # ------------------------------------------------------------------

    def claim_capture(
        self,
        review_key: str,
        source_label: str,
        source_kind: str,
        text_hash: str,
        session_id: str = "",
        turn_id: str = "",
    ) -> bool:
        """Atomically claim one extraction review.

        A new key is claimed immediately. ``failed`` rows and ``started`` rows
        older than five minutes are retried with an incremented attempt count.
        Fresh in-flight or terminal rows are left untouched and return ``False``.
        """
        key = _safe_identifier(review_key, allow_empty=False)
        now_ms = self._now_ms()
        stale_before_ms = now_ms - _CAPTURE_STALE_AFTER_MS
        with self._lock:
            conn = self._connection()
            if conn is None:
                return False
            cur = conn.execute(
                """
                INSERT INTO wiki_extraction_audit (
                    review_key, source_label, source_kind, text_hash,
                    session_id, turn_id, status, candidate_count, provider,
                    duration_ms, error_code, attempts, created_ms, updated_ms,
                    started_ms, finished_ms
                ) VALUES (?, ?, ?, ?, ?, ?, 'started', 0, '', 0, '', 1, ?, ?, ?, NULL)
                ON CONFLICT(review_key) DO UPDATE SET
                    source_label = excluded.source_label,
                    source_kind = excluded.source_kind,
                    text_hash = excluded.text_hash,
                    session_id = excluded.session_id,
                    turn_id = excluded.turn_id,
                    status = 'started',
                    candidate_count = 0,
                    provider = '',
                    duration_ms = 0,
                    error_code = '',
                    attempts = wiki_extraction_audit.attempts + 1,
                    updated_ms = excluded.updated_ms,
                    started_ms = excluded.started_ms,
                    finished_ms = NULL
                WHERE wiki_extraction_audit.status = 'failed'
                   OR (wiki_extraction_audit.status = 'started'
                       AND wiki_extraction_audit.updated_ms <= ?)
                """,
                (
                    key,
                    _safe_label(source_label),
                    _safe_token(source_kind, fallback="unknown"),
                    _safe_text_hash(text_hash),
                    _safe_identifier(session_id, allow_empty=True),
                    _safe_identifier(turn_id, allow_empty=True),
                    now_ms,
                    now_ms,
                    now_ms,
                    stale_before_ms,
                ),
            )
            conn.commit()
            return cur.rowcount == 1

    def finish_capture(
        self,
        review_key: str,
        status: CaptureStatus,
        candidate_count: int = 0,
        provider: str = "",
        duration_ms: int = 0,
        error_code: str = "",
    ) -> bool:
        """Finish a claimed review with bounded, non-content audit metadata."""
        if status not in _CAPTURE_STATUSES:
            raise ValueError(f"unknown capture status: {status!r}")
        if status not in _CAPTURE_FINISH_STATUSES:
            raise ValueError("finish_capture requires a terminal or failed status")
        key = _safe_identifier(review_key, allow_empty=False)
        now_ms = self._now_ms()
        count = max(0, int(candidate_count)) if status == "candidates" else 0
        safe_error = (
            _safe_error_code(error_code)
            if status in {"filtered", "failed"}
            else ""
        )
        with self._lock:
            conn = self._connection()
            if conn is None:
                return False
            cur = conn.execute(
                "UPDATE wiki_extraction_audit SET status = ?, candidate_count = ?, "
                "provider = ?, duration_ms = ?, error_code = ?, updated_ms = ?, "
                "finished_ms = ? WHERE review_key = ? AND status = 'started'",
                (
                    status,
                    count,
                    _safe_token(provider, fallback="", allow_empty=True),
                    max(0, int(duration_ms)),
                    safe_error,
                    now_ms,
                    now_ms,
                    key,
                ),
            )
            conn.commit()
            return cur.rowcount == 1

    def capture_seen(self, review_key: str) -> bool:
        """Return ``True`` only when a capture reached a terminal status."""
        return self.capture_status(review_key) in _CAPTURE_TERMINAL_STATUSES

    def capture_status(self, review_key: str) -> CaptureStatus | None:
        """Return the durable state for one review key, if it exists."""
        key = _safe_identifier(review_key, allow_empty=False)
        with self._lock:
            conn = self._connection()
            if conn is None:
                return None
            row = conn.execute(
                "SELECT status FROM wiki_extraction_audit WHERE review_key = ?",
                (key,),
            ).fetchone()
            return row[0] if row and row[0] in _CAPTURE_STATUSES else None

    def capture_summary(self, window_hours: int = 24) -> dict[str, int]:
        """Return fixed-shape aggregate capture telemetry for a recent window."""
        hours = max(1, int(window_hours))
        since_ms = self._now_ms() - hours * 60 * 60 * 1000
        empty = {
            "window_hours": hours,
            "total": 0,
            "started": 0,
            "filtered": 0,
            "empty": 0,
            "candidates": 0,
            "failed": 0,
            "facts": 0,
            "sessions_swept": 0,
        }
        with self._lock:
            conn = self._connection()
            if conn is None:
                return empty
            row = conn.execute(
                """
                SELECT
                    COUNT(*),
                    SUM(CASE WHEN status = 'started' THEN 1 ELSE 0 END),
                    SUM(CASE WHEN status = 'filtered' THEN 1 ELSE 0 END),
                    SUM(CASE WHEN status = 'empty' THEN 1 ELSE 0 END),
                    SUM(CASE WHEN status = 'candidates' THEN 1 ELSE 0 END),
                    SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END),
                    COALESCE(SUM(candidate_count), 0),
                    COUNT(DISTINCT CASE
                        WHEN REPLACE(source_kind, '_', '-') = 'session-sweep'
                             AND session_id != '' THEN session_id
                        ELSE NULL END)
                FROM wiki_extraction_audit
                WHERE updated_ms >= ?
                """,
                (since_ms,),
            ).fetchone()
        if row is None:
            return empty
        keys = tuple(empty)[1:]
        return {
            "window_hours": hours,
            **{
                key: int(value or 0)
                for key, value in zip(keys, row, strict=True)
            },
        }

    def capture_decision_summary(
        self,
        review_keys: Sequence[str],
    ) -> dict[str, int | list[str]]:
        """Return Stage-2 outcomes for candidates tied to selected reviews."""
        empty: dict[str, int | list[str]] = {
            "candidate_rows": 0,
            "pending": 0,
            "consolidated": 0,
            "rejected": 0,
            "skipped": 0,
            "add": 0,
            "update": 0,
            "noop": 0,
            "invalidate": 0,
            "pages_touched": [],
        }
        keys = tuple(
            dict.fromkeys(
                _safe_identifier(key, allow_empty=False) for key in review_keys if key
            )
        )
        if not keys:
            return empty
        placeholders = ",".join("?" for _ in keys)
        with self._lock:
            conn = self._connection()
            if conn is None:
                return empty
            query = (
                "SELECT j.status, j.decision, j.target_path "  # noqa: S608
                "FROM wiki_candidate_journal AS j "
                "JOIN wiki_candidate_capture AS c ON c.candidate_id = j.id "
                f"WHERE c.review_key IN ({placeholders}) ORDER BY j.id"
            )
            rows = conn.execute(query, keys).fetchall()
        result = dict(empty)
        pages: set[str] = set()
        result["candidate_rows"] = len(rows)
        for status, decision, target_path in rows:
            if status in {"pending", "consolidated", "rejected", "skipped"}:
                result[status] = int(result[status]) + 1
            if status == "consolidated" and decision in {
                "add",
                "update",
                "noop",
                "invalidate",
            }:
                result[decision] = int(result[decision]) + 1
            if status == "consolidated" and target_path:
                pages.add(str(target_path))
        result["pages_touched"] = sorted(pages)
        return result

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

    def oldest_pending_ms(self) -> int | None:
        """``created_ms`` of the oldest pending row, or ``None`` when none
        pending (spec A4 — drives the age-based flush so a quiet fresh
        install still produces pages below the count threshold)."""
        with self._lock:
            conn = self._connection()
            if conn is None:
                return None
            row = conn.execute(
                "SELECT MIN(created_ms) FROM wiki_candidate_journal "
                "WHERE status = 'pending'"
            ).fetchone()
            return int(row[0]) if row and row[0] is not None else None

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

    def _now_ms(self) -> int:
        return int(self._clock() * 1000)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def _normalise_candidates(
    facts: Sequence[CandidateFact],
) -> tuple[CandidateFact, ...]:
    """Bound and sanitize model-derived rows before any durable write."""
    accepted: list[CandidateFact] = []
    rejected_secret = 0
    for candidate in facts:
        fact = str(candidate.fact or "").strip()
        if not fact or len(fact) > _MAX_FACT_CHARS:
            continue
        if contains_secret(fact):
            rejected_secret += 1
            continue
        accepted.append(
            CandidateFact(
                fact=fact,
                kind=str(candidate.kind or "other").strip().lower(),
                subjects=normalise_subjects(candidate.subjects),
                evidence_turn_id=str(candidate.evidence_turn_id or "").strip(),
                evidence_excerpt=safe_preview(
                    candidate.evidence_excerpt,
                    max_chars=_MAX_EVIDENCE_CHARS,
                ).strip(),
            )
        )
    if rejected_secret:
        log.warning(
            "CandidateJournal: blocked %d candidate(s) containing secret-shaped data",
            rejected_secret,
        )
    return tuple(accepted)


def normalise_subjects(subjects: Sequence[object]) -> tuple[str, ...]:
    """Return bounded kebab-case slugs safe for vault-neighbour lookup."""
    accepted: list[str] = []
    for raw in subjects:
        slug = str(raw or "").strip().lower()
        if not _SAFE_SUBJECT_RE.fullmatch(slug) or slug in accepted:
            continue
        accepted.append(slug)
        if len(accepted) >= _MAX_SUBJECTS:
            break
    return tuple(accepted)


def _safe_identifier(value: str, *, allow_empty: bool) -> str:
    text = str(value or "").strip()
    if not text:
        if allow_empty:
            return ""
        raise ValueError("audit identifier must not be empty")
    if _SAFE_IDENTIFIER_RE.fullmatch(text) and not contains_secret(text):
        return text
    return f"opaque:{_digest(text)}"


def _safe_label(value: str) -> str:
    text = str(value or "").strip()
    if _SAFE_IDENTIFIER_RE.fullmatch(text) and not contains_secret(text):
        return text
    return f"opaque:{_digest(text)[:24]}"


def _safe_token(value: str, *, fallback: str, allow_empty: bool = False) -> str:
    text = str(value or "").strip()
    if not text:
        return "" if allow_empty else fallback
    if _SAFE_TOKEN_RE.fullmatch(text) and not contains_secret(text):
        return text
    return fallback


def _safe_text_hash(value: str) -> str:
    text = str(value or "").strip()
    if _SAFE_HASH_RE.fullmatch(text):
        return text.lower()
    return _digest(text)


def _safe_error_code(value: str) -> str:
    code = str(value or "").strip().lower()
    return code if code in _KNOWN_ERROR_CODES else "other"


__all__ = [
    "CandidateFact",
    "CandidateJournal",
    "CaptureStatus",
    "JournalRow",
    "normalise_subjects",
]
