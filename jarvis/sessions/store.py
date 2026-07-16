"""SQLite store for voice sessions (sync, WAL mode).

Unlike ``jarvis/missions/event_store.py`` (aiosqlite, async),
this store runs synchronously with ``sqlite3`` + ``threading.Lock``.
Rationale: the ``SessionRecorder`` is called from EventBus subscriber
callbacks; depending on the event this happens from the asyncio loop
OR from a worker thread (the pipeline is multi-threaded). A
synchronous store with a lock is the simplest path without loop-detection
hacks. SQLite writes are in the µs range, and lock contention is
unmeasurable on a voice path with < 100 events/sec.

Atomicity: WAL + ``synchronous=NORMAL`` flushes writes to disk;
an event can be lost between a process crash and the bus publish
- acceptable for voice sessions (a non-critical UI feature).

Cleanup: ``prune_older_than(days)`` deletes old sessions; ON DELETE
CASCADE in schema.sql cleans up turns + events automatically along with it.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .constants import VOICE_MODE_UNKNOWN
from .models import SessionListItem, VoiceEventRow, VoiceSessionRow, VoiceTurnRow

log = logging.getLogger(__name__)

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"


class SessionStore:
    """Sync SQLite store with threading.Lock synchronization."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()

    # --- Lifecycle ----------------------------------------------------

    def open(self) -> None:
        """Open the DB, set PRAGMAs, load the schema (idempotent), run migrations."""
        with self._lock:
            if self._conn is not None:
                return
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            # check_same_thread=False: we serialize ourselves via self._lock,
            # multiple threads (pipeline worker, FastAPI routes) may read+write.
            conn = sqlite3.connect(
                str(self._db_path),
                check_same_thread=False,
                isolation_level=None,  # autocommit; WAL is the lock manager
            )
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA foreign_keys=ON")
            schema_sql = _SCHEMA_PATH.read_text(encoding="utf-8")
            conn.executescript(schema_sql)
            conn.row_factory = sqlite3.Row
            self._conn = conn
            self._apply_migrations()
            log.debug("SessionStore opened: %s", self._db_path)

    def _apply_migrations(self) -> None:
        """Idempotent column migrations for pre-existing DBs.

        SQLite has no ``ADD COLUMN IF NOT EXISTS`` — we read
        ``pragma_table_info`` and only append missing columns.
        Pattern adopted from ``jarvis/missions/event_store.py``.
        """
        assert self._conn is not None
        session_columns = {
            str(row["name"])
            for row in self._conn.execute("PRAGMA table_info(voice_sessions)").fetchall()
        }
        if "voice_mode" not in session_columns:
            self._conn.execute(
                "ALTER TABLE voice_sessions "
                "ADD COLUMN voice_mode TEXT NOT NULL DEFAULT 'unknown'"
            )
            log.info("SessionStore migration: added voice_sessions.voice_mode")

        # Backfill only unresolved rows. The latest explicit runtime event wins,
        # which makes a ListeningStarted after a successful realtime handshake
        # a truthful classic-pipeline fallback. Older recorder versions did not
        # always persist RealtimeSessionReady, so a realtime turn tier is the
        # final evidence source. Everything else stays honestly unknown.
        self._conn.execute(
            """
            UPDATE voice_sessions AS s
            SET voice_mode = COALESCE(
                (
                    SELECT CASE e.kind
                        WHEN 'RealtimeSessionReady' THEN 'realtime'
                        WHEN 'ListeningStarted' THEN 'pipeline'
                    END
                    FROM voice_events AS e
                    WHERE e.session_id = s.id
                      AND e.kind IN ('RealtimeSessionReady', 'ListeningStarted')
                    ORDER BY e.seq DESC
                    LIMIT 1
                ),
                CASE WHEN EXISTS (
                    SELECT 1
                    FROM voice_turns AS t
                    WHERE t.session_id = s.id AND t.tier = 'realtime'
                ) THEN 'realtime' ELSE 'unknown' END
            )
            WHERE voice_mode IS NULL OR voice_mode IN ('', 'unknown')
            """
        )

        cur = self._conn.execute("PRAGMA table_info(voice_turns)")
        existing = {str(row["name"]) for row in cur.fetchall()}
        if "think_ms" not in existing:
            self._conn.execute(
                "ALTER TABLE voice_turns ADD COLUMN think_ms INTEGER NOT NULL DEFAULT 0"
            )
            log.info("SessionStore migration: added voice_turns.think_ms")
        if "speak_ms" not in existing:
            self._conn.execute(
                "ALTER TABLE voice_turns ADD COLUMN speak_ms INTEGER NOT NULL DEFAULT 0"
            )
            log.info("SessionStore migration: added voice_turns.speak_ms")
        if "awaiting_confirmation" not in existing:
            self._conn.execute(
                "ALTER TABLE voice_turns "
                "ADD COLUMN awaiting_confirmation INTEGER NOT NULL DEFAULT 0"
            )
            log.info("SessionStore migration: added voice_turns.awaiting_confirmation")

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    @property
    def _c(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("SessionStore: call open() before use")
        return self._conn

    # --- Session-Header ----------------------------------------------

    def upsert_session(
        self,
        *,
        session_id: str,
        started_ms: int,
        wake_keyword: str = "",
        language: str = "de",
        voice_mode: str = VOICE_MODE_UNKNOWN,
    ) -> None:
        """Create, or (on a recorder re-init) idempotently re-upsert.

        ON CONFLICT: ``started_ms`` is preserved (the first wake wins).
        Identity metadata may be refreshed, but an unresolved default mode
        never overwrites runtime evidence already recorded for the session.
        """
        with self._lock:
            self._c.execute(
                """
                INSERT INTO voice_sessions
                    (id, started_ms, wake_keyword, language, voice_mode)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    wake_keyword = excluded.wake_keyword,
                    language     = excluded.language,
                    voice_mode   = CASE
                        WHEN excluded.voice_mode != 'unknown'
                        THEN excluded.voice_mode
                        ELSE voice_sessions.voice_mode
                    END
                """,
                (session_id, started_ms, wake_keyword, language, voice_mode),
            )

    def update_session_voice_mode(self, *, session_id: str, voice_mode: str) -> None:
        """Persist runtime evidence for the effective voice engine.

        The column deliberately accepts future strings. Current recorder callers
        use only the canonical values from ``jarvis.sessions.constants``.
        """
        normalized = str(voice_mode or VOICE_MODE_UNKNOWN)
        with self._lock:
            self._c.execute(
                "UPDATE voice_sessions SET voice_mode = ? WHERE id = ?",
                (normalized, session_id),
            )

    def finalize_session(
        self,
        *,
        session_id: str,
        ended_ms: int,
        hangup_reason: str,
        turn_count: int,
        total_cost_usd: float,
        total_tokens_in: int,
        total_tokens_out: int,
        providers_used: list[str],
    ) -> None:
        """Hangup update — writes the end time and aggregates."""
        with self._lock:
            self._c.execute(
                """
                UPDATE voice_sessions SET
                    ended_ms         = ?,
                    hangup_reason    = ?,
                    turn_count       = ?,
                    total_cost_usd   = ?,
                    total_tokens_in  = ?,
                    total_tokens_out = ?,
                    providers_used   = ?
                WHERE id = ?
                """,
                (
                    ended_ms,
                    hangup_reason,
                    turn_count,
                    total_cost_usd,
                    total_tokens_in,
                    total_tokens_out,
                    json.dumps(sorted(set(providers_used))),
                    session_id,
                ),
            )

    # --- Turns --------------------------------------------------------

    def upsert_turn(
        self,
        *,
        turn_id: str,
        session_id: str,
        idx: int,
        started_ms: int,
    ) -> None:
        with self._lock:
            self._c.execute(
                """
                INSERT INTO voice_turns
                    (id, session_id, idx, started_ms)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    idx = excluded.idx,
                    started_ms = MIN(voice_turns.started_ms, excluded.started_ms)
                """,
                (turn_id, session_id, idx, started_ms),
            )

    def finalize_turn(
        self,
        *,
        turn_id: str,
        ended_ms: int,
        user_text: str,
        user_lang: str,
        jarvis_text: str,
        jarvis_lang: str,
        tier: str,
        provider: str,
        model: str,
        tokens_in: int,
        tokens_out: int,
        cost_usd: float,
        latency_total_ms: int,
        tool_calls: list[str],
        think_ms: int = 0,
        speak_ms: int = 0,
        awaiting_confirmation: bool = False,
    ) -> None:
        with self._lock:
            self._c.execute(
                """
                UPDATE voice_turns SET
                    ended_ms              = ?,
                    user_text             = ?,
                    user_lang             = ?,
                    jarvis_text           = ?,
                    jarvis_lang           = ?,
                    tier                  = ?,
                    provider              = ?,
                    model                 = ?,
                    tokens_in             = ?,
                    tokens_out            = ?,
                    cost_usd              = ?,
                    latency_total_ms      = ?,
                    think_ms              = ?,
                    speak_ms              = ?,
                    awaiting_confirmation = ?,
                    tool_calls_json       = ?
                WHERE id = ?
                """,
                (
                    ended_ms,
                    user_text,
                    user_lang,
                    jarvis_text,
                    jarvis_lang,
                    tier,
                    provider,
                    model,
                    tokens_in,
                    tokens_out,
                    cost_usd,
                    latency_total_ms,
                    think_ms,
                    speak_ms,
                    1 if awaiting_confirmation else 0,
                    json.dumps(tool_calls),
                    turn_id,
                ),
            )

    # --- Events -------------------------------------------------------

    def append_event(
        self,
        *,
        session_id: str,
        turn_id: str | None,
        ts_ms: int,
        kind: str,
        payload: dict[str, Any],
    ) -> int:
        """Append a raw event, returns the assigned seq."""
        with self._lock:
            cur = self._c.execute(
                """
                INSERT INTO voice_events
                    (session_id, turn_id, ts_ms, kind, payload_json)
                VALUES (?, ?, ?, ?, ?)
                RETURNING seq
                """,
                (session_id, turn_id, ts_ms, kind, json.dumps(payload, default=_json_default)),
            )
            row = cur.fetchone()
            return int(row["seq"])

    # --- Read-API -----------------------------------------------------

    def list_sessions(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[SessionListItem]:
        """Sessions by started_ms desc — newest first.

        ``preview`` is the first user utterance (the first turn row with
        a non-empty ``user_text``).
        """
        with self._lock:
            cur = self._c.execute(
                """
                SELECT s.*,
                       (SELECT t.user_text
                        FROM voice_turns t
                        WHERE t.session_id = s.id
                          AND t.user_text != ''
                        ORDER BY t.idx ASC
                        LIMIT 1) AS preview
                FROM voice_sessions s
                ORDER BY s.started_ms DESC
                LIMIT ? OFFSET ?
                """,
                (limit, max(0, int(offset))),
            )
            rows = cur.fetchall()
        # Per-row validation in a try/except: a single invalid row (e.g.
        # a hangup_reason value that drifted ahead of the Pydantic
        # Literal — see BUG-008) MUST NOT empty the whole list. We log
        # a structured warning so the drift is visible in operations,
        # then skip just the bad row. The list-API thus degrades to
        # "missing some rows" instead of "HTTP 500, empty UI".
        result: list[SessionListItem] = []
        for r in rows:
            duration_s = (
                (r["ended_ms"] - r["started_ms"]) / 1000.0
                if r["ended_ms"] is not None
                else None
            )
            try:
                item = SessionListItem(
                    id=r["id"],
                    started_ms=r["started_ms"],
                    ended_ms=r["ended_ms"],
                    hangup_reason=r["hangup_reason"] or "",
                    turn_count=r["turn_count"],
                    total_cost_usd=r["total_cost_usd"],
                    total_tokens_in=r["total_tokens_in"],
                    total_tokens_out=r["total_tokens_out"],
                    providers_used=json.loads(r["providers_used"] or "[]"),
                    language=r["language"],
                    wake_keyword=r["wake_keyword"],
                    voice_mode=r["voice_mode"] or VOICE_MODE_UNKNOWN,
                    duration_s=duration_s,
                    preview=_truncate(r["preview"] or "", 120),
                )
            except ValidationError as exc:
                log.warning(
                    "hangup_reason_drift_skipped: id=%s hangup_reason=%r err=%s",
                    r["id"],
                    r["hangup_reason"],
                    exc.errors(include_url=False, include_input=False),
                )
                continue
            result.append(item)
        return result

    def get_session(self, session_id: str) -> VoiceSessionRow | None:
        with self._lock:
            cur = self._c.execute(
                "SELECT * FROM voice_sessions WHERE id = ?", (session_id,)
            )
            r = cur.fetchone()
        if r is None:
            return None
        return _row_to_session(r)

    def get_turns(self, session_id: str) -> list[VoiceTurnRow]:
        with self._lock:
            cur = self._c.execute(
                """
                SELECT * FROM voice_turns
                WHERE session_id = ?
                ORDER BY idx ASC
                """,
                (session_id,),
            )
            rows = cur.fetchall()
        return [_row_to_turn(r) for r in rows]

    def get_latest_user_turn(
        self,
        *,
        session_id: str | None = None,
    ) -> VoiceTurnRow | None:
        """Return the newest persisted turn with a non-empty user transcript.

        ``started_ms`` is the durable utterance-order signal. The optional
        session filter lets callers avoid crossing conversation boundaries;
        without it, this is the newest voice transcript in the store.
        """
        with self._lock:
            cur = self._c.execute(
                """
                SELECT * FROM voice_turns
                WHERE TRIM(user_text) != ''
                  AND (? IS NULL OR session_id = ?)
                ORDER BY started_ms DESC, idx DESC, id DESC
                LIMIT 1
                """,
                (session_id, session_id),
            )
            row = cur.fetchone()
        return _row_to_turn(row) if row is not None else None

    def get_events(self, session_id: str) -> list[VoiceEventRow]:
        with self._lock:
            cur = self._c.execute(
                """
                SELECT * FROM voice_events
                WHERE session_id = ?
                ORDER BY seq ASC
                """,
                (session_id,),
            )
            rows = cur.fetchall()
        return [
            VoiceEventRow(
                seq=r["seq"],
                session_id=r["session_id"],
                turn_id=r["turn_id"],
                ts_ms=r["ts_ms"],
                kind=r["kind"],
                payload=json.loads(r["payload_json"] or "{}"),
            )
            for r in rows
        ]

    def list_open_sessions(self) -> list[str]:
        """IDs of all sessions without ``ended_ms`` — for crash recovery."""
        with self._lock:
            cur = self._c.execute(
                "SELECT id FROM voice_sessions WHERE ended_ms IS NULL"
            )
            return [r["id"] for r in cur.fetchall()]

    # --- Maintenance --------------------------------------------------

    def prune_older_than(self, days: int) -> int:
        """Deletes sessions with ``started_ms`` older than N days.

        Cascade delete in schema.sql cleans up turns + events
        automatically along with it. Returns the number of deleted session rows.

        The cutoff is recorded as the ``prune_horizon_ms`` high-water mark in
        ``store_meta`` (monotonically increasing) so downstream aggregators can
        distinguish "deleted below this instant" from "never existed" and stop
        recomputing days from a half-pruned source.
        """
        if days <= 0:
            return 0
        cutoff_ms = _now_ms() - days * 86_400_000
        with self._lock:
            cur = self._c.execute(
                "DELETE FROM voice_sessions WHERE started_ms < ?",
                (cutoff_ms,),
            )
            self._c.execute(
                """
                INSERT INTO store_meta (key, value) VALUES ('prune_horizon_ms', ?)
                ON CONFLICT(key) DO UPDATE SET
                    -- Cast BOTH sides: the column has TEXT affinity, and in
                    -- SQLite's cross-type ordering TEXT always sorts above
                    -- INTEGER, so MAX(text, int) would pick the text blindly.
                    value = MAX(
                        CAST(store_meta.value AS INTEGER),
                        CAST(excluded.value AS INTEGER)
                    )
                """,
                (cutoff_ms,),
            )
            return cur.rowcount

    def prune_horizon_ms(self) -> int | None:
        """Highest retention cutoff ever applied, or ``None`` if never pruned."""
        with self._lock:
            row = self._c.execute(
                "SELECT value FROM store_meta WHERE key = 'prune_horizon_ms'"
            ).fetchone()
        if row is None:
            return None
        try:
            return int(row["value"])
        except (TypeError, ValueError):
            return None

    def last_activity_ms(self, session_id: str) -> int | None:
        """Latest recorded timestamp of a session: raw events plus turn
        start/end. ``None`` when the session left no trace beyond its header.
        Used to seal crashed sessions honestly instead of stamping boot time.
        """
        with self._lock:
            row = self._c.execute(
                """
                SELECT MAX(ts) AS ts FROM (
                    SELECT MAX(ts_ms) AS ts FROM voice_events WHERE session_id = ?
                    UNION ALL
                    SELECT MAX(MAX(started_ms), COALESCE(MAX(ended_ms), 0)) AS ts
                    FROM voice_turns WHERE session_id = ?
                )
                """,
                (session_id, session_id),
            ).fetchone()
        ts = row["ts"] if row is not None else None
        return int(ts) if ts else None

    def repair_crash_seals(self, *, tolerance_ms: int = 3_600_000) -> int:
        """Re-seal ``shutdown`` sessions whose recorded end drifted more than
        ``tolerance_ms`` past their last real activity.

        Crash recovery used to stamp ``ended_ms = boot time``, so a session
        that died in the evening and was recovered the next morning carried
        14+ phantom hours into every duration-based stat. Idempotent: honest
        seals are inside the tolerance and stay untouched. Returns the number
        of repaired rows.
        """
        with self._lock:
            rows = self._c.execute(
                "SELECT id, started_ms, ended_ms FROM voice_sessions "
                "WHERE hangup_reason = 'shutdown' AND ended_ms IS NOT NULL"
            ).fetchall()
        repaired = 0
        for r in rows:
            last = self.last_activity_ms(r["id"])
            honest_end = max(last or 0, int(r["started_ms"]))
            if int(r["ended_ms"]) > honest_end + tolerance_ms:
                with self._lock:
                    self._c.execute(
                        "UPDATE voice_sessions SET ended_ms = ? WHERE id = ?",
                        (honest_end, r["id"]),
                    )
                repaired += 1
        return repaired

    def wal_checkpoint(self) -> None:
        with self._lock:
            self._c.execute("PRAGMA wal_checkpoint(TRUNCATE)")


# --- Helpers ----------------------------------------------------------


def _truncate(s: str, n: int) -> str:
    if len(s) <= n:
        return s
    return s[: n - 1] + "…"


def _now_ms() -> int:
    import time

    return int(time.time() * 1000)


def _json_default(obj: Any) -> Any:
    """Fallback for json.dumps: UUID, set, dataclass-ish."""
    if hasattr(obj, "hex") and hasattr(obj, "int"):  # UUID
        return str(obj)
    if isinstance(obj, set):
        return sorted(obj)
    return repr(obj)


def _row_to_session(r: sqlite3.Row) -> VoiceSessionRow:
    return VoiceSessionRow(
        id=r["id"],
        started_ms=r["started_ms"],
        ended_ms=r["ended_ms"],
        hangup_reason=r["hangup_reason"] or "",
        turn_count=r["turn_count"],
        total_cost_usd=r["total_cost_usd"],
        total_tokens_in=r["total_tokens_in"],
        total_tokens_out=r["total_tokens_out"],
        providers_used=json.loads(r["providers_used"] or "[]"),
        language=r["language"],
        wake_keyword=r["wake_keyword"],
        voice_mode=r["voice_mode"] or VOICE_MODE_UNKNOWN,
    )


def _row_to_turn(r: sqlite3.Row) -> VoiceTurnRow:
    return VoiceTurnRow(
        id=r["id"],
        session_id=r["session_id"],
        idx=r["idx"],
        started_ms=r["started_ms"],
        ended_ms=r["ended_ms"],
        user_text=r["user_text"],
        user_lang=r["user_lang"],
        jarvis_text=r["jarvis_text"],
        jarvis_lang=r["jarvis_lang"],
        tier=r["tier"],
        provider=r["provider"],
        model=r["model"],
        tokens_in=r["tokens_in"],
        tokens_out=r["tokens_out"],
        cost_usd=r["cost_usd"],
        latency_total_ms=r["latency_total_ms"],
        think_ms=_safe_col(r, "think_ms", 0),
        speak_ms=_safe_col(r, "speak_ms", 0),
        # SQLite stores this as INTEGER 0/1; bool() is intentional (0 -> False,
        # 1 -> True). Keep _safe_col's contract at 0/1 — a sentinel like -1 would
        # break the bool() mapping here.
        awaiting_confirmation=bool(_safe_col(r, "awaiting_confirmation", 0)),
        tool_calls=json.loads(r["tool_calls_json"] or "[]"),
    )


def _safe_col(r: sqlite3.Row, name: str, default: int) -> int:
    """Reads a column, returns the default if it doesn't exist.

    We only need this for the narrow window in which a running
    backend still starts without the migration — harmless in the normal case.
    """
    try:
        v = r[name]
        return int(v) if v is not None else default
    except (KeyError, IndexError):
        return default


# Suppress unused import warning for typing-only Iterable
_ = Iterable

__all__ = ["SessionStore"]
