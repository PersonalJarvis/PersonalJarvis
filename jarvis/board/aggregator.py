"""BoardAggregator — FlightRecorder JSONL → ``personal.db`` aggregation.

Phase A of the Jarvis Board plan (see ``docs/jarvis-board/RECON.md`` and
``docs/jarvis-board/ARCHITECTURE.md``).

## Responsibilities

1. Reads JSONL files from ``data/flight_recorder/``.
2. Groups events by day.
3. Writes aggregated safe fields into ``daily_stats`` (upsert).
4. Maintains ``personal_records`` (highs per metric).
5. Provides ``export_all_for_federation()``, which guarantees that
   **no** PII fields are forwarded.

## What the aggregator does NOT do

- No network calls. Any external HTTP call would be a bug.
- Does not block the voice loop: ``run_forever()`` swallows all exceptions
  and only logs them — analogous to the FlightRecorder.
- No hot-path queries. That is the responsibility of the read-only ``BoardStore``.

## Why synchronous ``sqlite3`` instead of ``aiosqlite``

The aggregator is a batch job (every 6 h). The overhead of a synchronous
read/write operation is irrelevant for the voice pipeline because it runs
in its own ``asyncio.to_thread`` call and is only active during the sleep
interval. The API routes read in read-only mode with a short-lived connection
in WAL mode — readers do not block the writer.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import sqlite3
import threading
import time
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, tzinfo
from pathlib import Path
from typing import Any

from jarvis.board.categories import categorize_tool

log = logging.getLogger(__name__)

SCHEMA_FILE = Path(__file__).parent / "schema.sql"

# Window for the voice-retry heuristic. Two ``TranscriptFinal`` events
# closer than ``VOICE_RETRY_WINDOW_S`` seconds apart — the second is treated
# as a retry and reduces the ``first_try_rate``.
#
# 8 s is deliberately generous — faster than one response round-trip, but
# longer than typical STT buffering jitter. Will be replaced by a proper
# ``VoiceAttemptResult`` event in Phase B (see RECON.md §6.4).
VOICE_RETRY_WINDOW_S = 8.0
CONVERSATION_TRACE_CAP_S = 30 * 60.0

# Daily-stats upsert, two flavours sharing one column list. The full variant
# overwrites the day with the recompute; the insert-only variant backs a
# FROZEN day (below the retention-prune horizon): its already-recorded ledger
# row wins and only a first-time insert (board DB rebuilt from scratch) lands.
_UPSERT_COLUMNS_SQL = """
    INSERT INTO daily_stats (
        date, tasks_completed, tasks_failed, tools_used,
        unique_tools_count, voice_commands_count,
        voice_first_try_rate, hours_saved_estimate,
        active_events_count, conversation_seconds_estimate,
        user_words_count, jarvis_words_count, session_count,
        category_counts
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_UPSERT_INSERT_ONLY_SQL = _UPSERT_COLUMNS_SQL + "ON CONFLICT(date) DO NOTHING"

_UPSERT_FULL_SQL = _UPSERT_COLUMNS_SQL + """
    ON CONFLICT(date) DO UPDATE SET
        tasks_completed      = excluded.tasks_completed,
        tasks_failed         = excluded.tasks_failed,
        tools_used           = excluded.tools_used,
        unique_tools_count   = excluded.unique_tools_count,
        voice_commands_count = excluded.voice_commands_count,
        voice_first_try_rate = excluded.voice_first_try_rate,
        hours_saved_estimate = excluded.hours_saved_estimate,
        active_events_count  = excluded.active_events_count,
        conversation_seconds_estimate = excluded.conversation_seconds_estimate,
        user_words_count     = excluded.user_words_count,
        jarvis_words_count   = excluded.jarvis_words_count,
        session_count        = excluded.session_count,
        category_counts      = excluded.category_counts
"""

ACTIVE_EVENT_NAMES = {
    "ActionExecuted",
    "BrainTurnCompleted",
    "ListeningStarted",
    "MessageSent",
    "ResponseGenerated",
    "JarvisAgentBackgroundCompleted",
    "JarvisAgentTaskCompleted",
    "SystemStarted",
    "TaskCompleted",
    "TaskFailed",
    "TranscriptFinal",
    "UtteranceCaptured",
}

CONVERSATION_EVENT_NAMES = {
    "BrainTurnCompleted",
    "BrainTurnStarted",
    "ListeningStarted",
    "MessageSent",
    "ResponseGenerated",
    "TranscriptFinal",
    "UtteranceCaptured",
}


@dataclass
class DailyStats:
    """One entry in ``daily_stats``. Safe fields only."""
    date: str
    tasks_completed: int = 0
    tasks_failed: int = 0
    tools_used: list[str] = field(default_factory=list)
    voice_commands_count: int = 0
    voice_retries: int = 0             # internal only, converted to a rate
    hours_saved_estimate: float = 0.0
    active_events_count: int = 0
    conversation_seconds_estimate: float = 0.0
    user_words_count: int = 0
    jarvis_words_count: int = 0
    session_count: int = 0
    category_counts: dict[str, int] = field(default_factory=dict)

    @property
    def unique_tools_count(self) -> int:
        return len(self.tools_used)

    @property
    def voice_first_try_rate(self) -> float | None:
        if self.voice_commands_count == 0:
            return None
        successful = self.voice_commands_count - self.voice_retries
        return max(0.0, min(1.0, successful / self.voice_commands_count))


@dataclass
class PersonalRecord:
    metric: str
    value: float
    achieved_on: str
    context: dict[str, Any] = field(default_factory=dict)


class BoardAggregator:
    """Batch aggregator for FlightRecorder events.

    Typical usage::

        agg = BoardAggregator(
            jsonl_dir=Path("data/flight_recorder"),
            db_path=Path("data/board/personal.db"),
        )
        agg.run()                         # once, synchronously
        # or:
        await agg.run_forever(interval_s=6 * 3600)

    Tests pass ``jsonl_dir`` as ``tmp_path`` and can then query
    ``agg.db`` as a synchronous ``sqlite3.Connection``.
    """

    def __init__(
        self,
        jsonl_dir: Path,
        db_path: Path | None = None,
        sessions_db_path: Path | None = None,
        tz: tzinfo | None = None,
    ) -> None:
        self._jsonl_dir = Path(jsonl_dir)
        self._db_path = Path(db_path) if db_path is not None else (
            self._jsonl_dir.parent / "board" / "personal.db"
        )
        # Durable conversation store (voice_turns + voice_sessions). This is
        # the rich source the board actually has data in — the flight-recorder
        # JSONL is empty on most installs. ``None`` disables the source.
        self._sessions_db_path = (
            Path(sessions_db_path) if sessions_db_path is not None else None
        )
        # Day-bucketing timezone. ``None`` = the host's local timezone (the
        # meaningful granularity for a personal dashboard). Tests inject
        # explicit zones to prove the bucketing is deterministic and that
        # all-time totals are timezone-invariant.
        self._tz = tz
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db: sqlite3.Connection | None = None
        # Serialises every aggregation run. The connection is shared across
        # thread-pool threads (freshen-on-read + the 6 h loop + manual refresh),
        # so runs must never overlap — hence ``check_same_thread=False`` on the
        # connection paired with this lock.
        self._run_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    @property
    def db(self) -> sqlite3.Connection:
        """An open connection. Lazy, idempotent."""
        if self._db is None:
            self._db = sqlite3.connect(
                self._db_path, isolation_level=None, check_same_thread=False
            )
            self._db.row_factory = sqlite3.Row
            schema = SCHEMA_FILE.read_text(encoding="utf-8")
            self._db.executescript(schema)
            self._ensure_schema(self._db)
        return self._db

    def close(self) -> None:
        if self._db is not None:
            with contextlib.suppress(sqlite3.Error):
                self._db.close()
            self._db = None

    def run(self) -> None:
        """One complete aggregation run. Synchronous, idempotent, serialised.

        Error handling: all expected I/O errors are logged, not raised. The
        caller (``run_forever``) would otherwise kill the background task and
        subsequently block voice-loop telemetry — this is explicitly excluded
        in the Plan §5-A done criteria.
        """
        with self._run_lock:
            self._run_body()

    def run_if_stale(self, ttl_s: float) -> bool:
        """Re-aggregate only if the last run is older than ``ttl_s`` seconds.

        This is the "live indicators" path: the board read endpoints call it on
        every poll so newly spoken words show up within a poll cycle, while the
        TTL gate plus a non-blocking lock keep concurrent polls from triggering
        a thundering herd of re-aggregations. Returns ``True`` if it ran.
        """
        if not self._run_lock.acquire(blocking=False):
            return False  # another run in progress — its result is fresh enough
        try:
            last = self._get_last_run_ns()
            if last is not None and (time.time_ns() - last) < int(ttl_s * 1e9):
                return False
            self._run_body()
            return True
        finally:
            self._run_lock.release()

    def _run_body(self) -> None:
        """The actual aggregation. Callers must hold ``self._run_lock``."""
        try:
            daily = self._aggregate_events()
            self._aggregate_sessions(daily)
            self._upsert_daily_stats(
                daily.values(), freeze_before_ms=self._read_prune_horizon()
            )
            self._refresh_personal_records()
            self._set_meta("last_run_ns", str(time.time_ns()))
        except Exception:  # noqa: BLE001
            log.exception("BoardAggregator.run() abgebrochen — DB bleibt unveraendert")

    def _get_last_run_ns(self) -> int | None:
        """Timestamp of the last successful run, or ``None`` if never run."""
        try:
            row = self.db.execute(
                "SELECT value FROM aggregator_meta WHERE key = 'last_run_ns'"
            ).fetchone()
        except sqlite3.Error:
            return None
        if row is None:
            return None
        try:
            return int(row["value"])
        except (ValueError, TypeError):
            return None

    async def run_forever(self, *, interval_s: float = 6 * 3600) -> None:
        """Infinite loop for the FastAPI lifecycle.

        ``run()`` executes in ``asyncio.to_thread`` so that the event loop is
        not blocked by file I/O. Each iteration is independent — a crash in
        ``run()`` is swallowed and retried at the next interval.
        """
        while True:
            try:
                await asyncio.to_thread(self.run)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("BoardAggregator-Loop: unerwartete Exception")
            try:
                await asyncio.sleep(interval_s)
            except asyncio.CancelledError:
                raise

    # ------------------------------------------------------------------
    # Federation-Safe Export
    # ------------------------------------------------------------------

    def export_all_for_federation(self) -> dict[str, Any]:
        """Returns ONLY aggregate fields. Guaranteed PII-free.

        This method is the sole official way to serialise board data for the
        Phase-C backend sync. It only touches the ``daily_stats`` and
        ``personal_records`` tables — raw events or user text are
        **never read here**.
        """
        daily_rows = self.db.execute(
            "SELECT date, tasks_completed, tasks_failed, tools_used, "
            "unique_tools_count, voice_commands_count, voice_first_try_rate, "
            "hours_saved_estimate, active_events_count, "
            "conversation_seconds_estimate FROM daily_stats ORDER BY date"
        ).fetchall()
        records_rows = self.db.execute(
            "SELECT metric, value, achieved_on FROM personal_records "
            "ORDER BY metric"
        ).fetchall()
        return {
            "daily_stats": [
                {
                    "date": r["date"],
                    "tasks_completed": r["tasks_completed"],
                    "tasks_failed": r["tasks_failed"],
                    "tools_used": json.loads(r["tools_used"] or "[]"),
                    "unique_tools_count": r["unique_tools_count"],
                    "voice_commands_count": r["voice_commands_count"],
                    "voice_first_try_rate": r["voice_first_try_rate"],
                    "hours_saved_estimate": r["hours_saved_estimate"],
                    "active_events_count": r["active_events_count"],
                    "conversation_seconds_estimate": r["conversation_seconds_estimate"],
                }
                for r in daily_rows
            ],
            "personal_records": [
                {
                    "metric": r["metric"],
                    "value": r["value"],
                    "achieved_on": r["achieved_on"],
                }
                for r in records_rows
            ],
        }

    # ------------------------------------------------------------------
    # Event-Parsing
    # ------------------------------------------------------------------

    def _iter_event_records(self) -> Iterable[dict[str, Any]]:
        """Iterates all JSONL lines in chronological order.

        Corrupted lines are logged and skipped. I/O errors on individual
        files (e.g. renamed during rotation) are silently ignored — the next
        run will see them again.
        """
        if not self._jsonl_dir.exists():
            return
        for path in sorted(self._jsonl_dir.glob("*.jsonl")):
            try:
                with path.open("r", encoding="utf-8") as fh:
                    for raw in fh:
                        line = raw.strip()
                        if not line:
                            continue
                        try:
                            record = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if not isinstance(record, dict) or "event" not in record:
                            continue
                        yield record
            except OSError:
                continue

    def _aggregate_events(self) -> dict[str, DailyStats]:
        """Groups all events into ``DailyStats`` per day."""
        daily: dict[str, DailyStats] = {}
        tools_per_day: dict[str, set[str]] = defaultdict(set)
        last_transcript_ns_per_day: dict[str, int] = {}
        conversation_points: dict[tuple[str, str], list[int]] = defaultdict(list)
        utterance_seconds: dict[tuple[str, str], float] = defaultdict(float)

        records = list(self._iter_event_records())
        records.sort(key=lambda r: r.get("ts_ns", 0))

        for record in records:
            ts_ns = int(record.get("ts_ns") or 0)
            if ts_ns <= 0:
                continue
            date = _iso_date_from_ns(ts_ns, self._tz)
            stats = daily.setdefault(date, DailyStats(date=date))
            event = record.get("event", "")
            payload = record.get("payload") or {}
            if not isinstance(payload, dict):
                payload = {}
            trace_id = str(record.get("trace_id") or "")

            if _is_active_event(event, payload):
                stats.active_events_count += 1

            if event in CONVERSATION_EVENT_NAMES and trace_id:
                key = (date, trace_id)
                conversation_points[key].append(ts_ns)
                if event == "UtteranceCaptured":
                    duration_ms = float(payload.get("duration_ms") or 0.0)
                    if duration_ms > 0:
                        utterance_seconds[key] += duration_ms / 1000.0

            if event == "TaskCompleted":
                stats.tasks_completed += 1
            elif event == "TaskFailed":
                stats.tasks_failed += 1
            elif event == "JarvisAgentTaskCompleted":
                if payload.get("success"):
                    stats.tasks_completed += 1
                else:
                    stats.tasks_failed += 1
                duration_s = float(payload.get("duration_s") or 0.0)
                if duration_s > 0:
                    stats.hours_saved_estimate += duration_s / 3600.0
            elif event == "ActionExecuted":
                if payload.get("success"):
                    tool = str(payload.get("tool_name") or "").strip()
                    if tool:
                        tools_per_day[date].add(tool)
            elif event == "TranscriptFinal":
                stats.voice_commands_count += 1
                last_ns = last_transcript_ns_per_day.get(date)
                if last_ns is not None:
                    delta_s = (ts_ns - last_ns) / 1e9
                    if 0 < delta_s < VOICE_RETRY_WINDOW_S:
                        stats.voice_retries += 1
                last_transcript_ns_per_day[date] = ts_ns

        for date, stats in daily.items():
            stats.tools_used = sorted(tools_per_day[date])
        for (date, _trace_id), points in conversation_points.items():
            points.sort()
            span_s = (points[-1] - points[0]) / 1e9 if len(points) > 1 else 0.0
            seconds = max(span_s, utterance_seconds.get((date, _trace_id), 0.0))
            if seconds > 0:
                daily[date].conversation_seconds_estimate += min(
                    seconds,
                    CONVERSATION_TRACE_CAP_S,
                )
        return daily

    # ------------------------------------------------------------------
    # Durable conversation store (sessions.db)
    # ------------------------------------------------------------------

    def _aggregate_sessions(self, daily: dict[str, DailyStats]) -> None:
        """Fold word counts, usage categories, session count and conversation
        time from ``sessions.db`` into the per-day stats.

        This is the source that actually has data — voice_turns holds the user
        and Jarvis text plus per-turn tool calls. Only counts are derived; the
        raw text never leaves this method. A missing or unreadable database is
        a silent no-op, consistent with the rest of the aggregator.
        """
        path = self._sessions_db_path
        if path is None or not Path(path).exists():
            return
        try:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
        except sqlite3.Error:
            log.warning("BoardAggregator: sessions.db not readable (%s)", path)
            return
        try:
            for row in conn.execute(
                "SELECT started_ms, user_text, jarvis_text, tool_calls_json "
                "FROM voice_turns"
            ):
                ms = row["started_ms"]
                if not ms:
                    continue
                date = _iso_date_from_ms(int(ms), self._tz)
                stats = daily.setdefault(date, DailyStats(date=date))
                user_words = _count_words(row["user_text"])
                jarvis_words = _count_words(row["jarvis_text"])
                stats.user_words_count += user_words
                stats.jarvis_words_count += jarvis_words
                if user_words or jarvis_words:
                    # Heatmap/streak should light up on real conversation days;
                    # voice_commands_count is left to the flight-recorder source
                    # so the voice_first_try_rate semantics stay intact.
                    stats.active_events_count += 1
                for tool in _parse_tool_calls(row["tool_calls_json"]):
                    cat = categorize_tool(tool)
                    stats.category_counts[cat] = stats.category_counts.get(cat, 0) + 1

            for row in conn.execute(
                "SELECT started_ms, ended_ms FROM voice_sessions"
            ):
                ms = row["started_ms"]
                if not ms:
                    continue
                date = _iso_date_from_ms(int(ms), self._tz)
                stats = daily.setdefault(date, DailyStats(date=date))
                stats.session_count += 1
                ended = row["ended_ms"]
                if ended and int(ended) > int(ms):
                    stats.conversation_seconds_estimate += (
                        int(ended) - int(ms)
                    ) / 1000.0
        except sqlite3.Error:
            log.exception("BoardAggregator: error reading sessions.db")
        finally:
            with contextlib.suppress(sqlite3.Error):
                conn.close()

    def _read_prune_horizon(self) -> int | None:
        """Retention-prune high-water mark recorded in ``sessions.db``.

        Every day whose local day-start lies below this instant may have lost
        source rows to the recorder's boot-time retention prune, so its
        already-recorded ledger row must never be overwritten with a recompute
        from the shrunken source. ``None`` (no sessions db, old schema, never
        pruned) keeps the historical full-overwrite behaviour.
        """
        path = self._sessions_db_path
        if path is None or not Path(path).exists():
            return None
        try:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        except sqlite3.Error:
            return None
        try:
            row = conn.execute(
                "SELECT value FROM store_meta WHERE key = 'prune_horizon_ms'"
            ).fetchone()
            return int(row[0]) if row is not None else None
        except (sqlite3.Error, TypeError, ValueError):
            return None
        finally:
            with contextlib.suppress(sqlite3.Error):
                conn.close()

    # ------------------------------------------------------------------
    # Upsert
    # ------------------------------------------------------------------

    def _day_start_ms(self, date: str) -> int:
        """Epoch ms of the day's midnight in the bucketing timezone."""
        day = datetime.fromisoformat(date)
        if self._tz is not None:
            day = day.replace(tzinfo=self._tz)
        else:
            day = day.astimezone()  # naive -> host-local midnight
        return int(day.timestamp() * 1000)

    def _upsert_daily_stats(
        self,
        rows: Iterable[DailyStats],
        *,
        freeze_before_ms: int | None = None,
    ) -> None:
        """Write per-day rows. Days at or below the prune horizon are frozen:
        their existing ledger row wins and only a first-time INSERT (board DB
        rebuilt from scratch) is allowed — a recompute from a source that lost
        rows to retention must never shrink an already-recorded day (this was
        the bug that made ACTIVE TIME decay day by day).
        """
        conn = self.db
        with conn:
            conn.execute("BEGIN")
            for stats in rows:
                frozen = (
                    freeze_before_ms is not None
                    and self._day_start_ms(stats.date) < freeze_before_ms
                )
                conn.execute(
                    _UPSERT_INSERT_ONLY_SQL if frozen else _UPSERT_FULL_SQL,
                    (
                        stats.date,
                        stats.tasks_completed,
                        stats.tasks_failed,
                        json.dumps(stats.tools_used),
                        stats.unique_tools_count,
                        stats.voice_commands_count,
                        stats.voice_first_try_rate,
                        stats.hours_saved_estimate,
                        stats.active_events_count,
                        stats.conversation_seconds_estimate,
                        stats.user_words_count,
                        stats.jarvis_words_count,
                        stats.session_count,
                        json.dumps(stats.category_counts),
                    ),
                )

    # ------------------------------------------------------------------
    # Personal Records
    # ------------------------------------------------------------------

    def _refresh_personal_records(self) -> None:
        """Derives personal records from ``daily_stats``.

        Not retroactive: if an old row subsequently becomes higher, the upsert
        overwrites the record — this is intentional, because Phase A
        re-aggregates idempotently.
        """
        conn = self.db
        candidates: list[PersonalRecord] = []

        row = conn.execute(
            "SELECT date, tasks_completed FROM daily_stats "
            "WHERE tasks_completed > 0 ORDER BY tasks_completed DESC, date ASC "
            "LIMIT 1"
        ).fetchone()
        if row is not None:
            candidates.append(
                PersonalRecord(
                    metric="most_tasks_in_a_day",
                    value=float(row["tasks_completed"]),
                    achieved_on=row["date"],
                )
            )

        row = conn.execute(
            "SELECT date, unique_tools_count FROM daily_stats "
            "WHERE unique_tools_count > 0 "
            "ORDER BY unique_tools_count DESC, date ASC LIMIT 1"
        ).fetchone()
        if row is not None:
            candidates.append(
                PersonalRecord(
                    metric="most_unique_tools_in_a_day",
                    value=float(row["unique_tools_count"]),
                    achieved_on=row["date"],
                )
            )

        row = conn.execute(
            "SELECT date, voice_commands_count FROM daily_stats "
            "WHERE voice_commands_count > 0 "
            "ORDER BY voice_commands_count DESC, date ASC LIMIT 1"
        ).fetchone()
        if row is not None:
            candidates.append(
                PersonalRecord(
                    metric="most_voice_commands_in_a_day",
                    value=float(row["voice_commands_count"]),
                    achieved_on=row["date"],
                )
            )

        row = conn.execute(
            "SELECT date, hours_saved_estimate FROM daily_stats "
            "WHERE hours_saved_estimate > 0 "
            "ORDER BY hours_saved_estimate DESC, date ASC LIMIT 1"
        ).fetchone()
        if row is not None:
            candidates.append(
                PersonalRecord(
                    metric="most_hours_saved_in_a_day",
                    value=float(row["hours_saved_estimate"]),
                    achieved_on=row["date"],
                )
            )

        row = conn.execute(
            "SELECT date, active_events_count FROM daily_stats "
            "WHERE active_events_count > 0 "
            "ORDER BY active_events_count DESC, date ASC LIMIT 1"
        ).fetchone()
        if row is not None:
            candidates.append(
                PersonalRecord(
                    metric="most_active_events_in_a_day",
                    value=float(row["active_events_count"]),
                    achieved_on=row["date"],
                )
            )

        row = conn.execute(
            "SELECT date, conversation_seconds_estimate FROM daily_stats "
            "WHERE conversation_seconds_estimate > 0 "
            "ORDER BY conversation_seconds_estimate DESC, date ASC LIMIT 1"
        ).fetchone()
        if row is not None:
            candidates.append(
                PersonalRecord(
                    metric="most_conversation_hours_in_a_day",
                    value=float(row["conversation_seconds_estimate"]) / 3600.0,
                    achieved_on=row["date"],
                )
            )

        with conn:
            conn.execute("BEGIN")
            for rec in candidates:
                conn.execute(
                    """
                    INSERT INTO personal_records (metric, value, achieved_on, context)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(metric) DO UPDATE SET
                        value       = excluded.value,
                        achieved_on = excluded.achieved_on,
                        context     = excluded.context
                    """,
                    (rec.metric, rec.value, rec.achieved_on, json.dumps(rec.context)),
                )

    # ------------------------------------------------------------------
    # Meta
    # ------------------------------------------------------------------

    def _set_meta(self, key: str, value: str) -> None:
        conn = self.db
        with conn:
            conn.execute(
                "INSERT INTO aggregator_meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        _ensure_daily_stats_columns(conn)


def _iso_date_from_ns(ts_ns: int, tz: tzinfo | None = None) -> str:
    """Converts a nanosecond timestamp to an ISO date in the given timezone.

    ``None`` means the host's local timezone — the meaningful granularity for
    a personal dashboard: a commit at 00:30 belongs to the user's "today",
    not "yesterday in UTC".
    """
    dt = datetime.fromtimestamp(ts_ns / 1e9, tz=UTC).astimezone(tz)
    return dt.strftime("%Y-%m-%d")


def _is_active_event(event: str, payload: dict[str, Any]) -> bool:
    if event not in ACTIVE_EVENT_NAMES:
        return False
    if event == "ActionExecuted":
        return bool(payload.get("success"))
    return True


def _iso_date_from_ms(ts_ms: int, tz: tzinfo | None = None) -> str:
    """ISO date from a millisecond epoch timestamp (``None`` = host-local)."""
    dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=UTC).astimezone(tz)
    return dt.strftime("%Y-%m-%d")


def _count_words(text: str | None) -> int:
    """Whitespace word count. ``None``/empty -> 0. Never stores the text."""
    if not text:
        return 0
    return len(text.split())


def _parse_tool_calls(raw: str | None) -> list[str]:
    """Extract tool names from a ``tool_calls_json`` cell.

    The column is a JSON array of either plain tool-name strings
    (``["spawn_openclaw"]``) or objects carrying a ``name`` key. Both shapes
    are tolerated; anything unparseable yields an empty list.
    """
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return []
    names: list[str] = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, str):
                names.append(item)
            elif isinstance(item, dict):
                name = item.get("name") or item.get("tool") or item.get("tool_name")
                if isinstance(name, str):
                    names.append(name)
    return names


# Migration columns added after the original Phase-A schema. Kept in one place
# so the aggregator (writer) and the BoardStore (reader) stay in lock-step —
# both call this on every connection open.
_DAILY_STATS_MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("active_events_count", "INTEGER NOT NULL DEFAULT 0"),
    ("conversation_seconds_estimate", "REAL NOT NULL DEFAULT 0.0"),
    ("user_words_count", "INTEGER NOT NULL DEFAULT 0"),
    ("jarvis_words_count", "INTEGER NOT NULL DEFAULT 0"),
    ("session_count", "INTEGER NOT NULL DEFAULT 0"),
    ("category_counts", "TEXT NOT NULL DEFAULT '{}'"),
)


def _ensure_daily_stats_columns(conn: sqlite3.Connection) -> None:
    """Idempotently add any missing ``daily_stats`` columns (no Alembic)."""
    existing = {
        row[1] for row in conn.execute("PRAGMA table_info(daily_stats)").fetchall()
    }
    for name, decl in _DAILY_STATS_MIGRATIONS:
        if name not in existing:
            conn.execute(f"ALTER TABLE daily_stats ADD COLUMN {name} {decl}")
