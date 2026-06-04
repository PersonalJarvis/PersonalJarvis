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
import time
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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

ACTIVE_EVENT_NAMES = {
    "ActionExecuted",
    "BrainTurnCompleted",
    "ListeningStarted",
    "MessageSent",
    "ResponseGenerated",
    "OpenClawBackgroundCompleted",
    "OpenClawTaskCompleted",
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
    ) -> None:
        self._jsonl_dir = Path(jsonl_dir)
        self._db_path = Path(db_path) if db_path is not None else (
            self._jsonl_dir.parent / "board" / "personal.db"
        )
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db: sqlite3.Connection | None = None

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    @property
    def db(self) -> sqlite3.Connection:
        """An open connection. Lazy, idempotent."""
        if self._db is None:
            # check_same_thread=False: run() is dispatched via asyncio.to_thread
            # (server.run_forever loop + the manual /board refresh route), which
            # uses the default ThreadPoolExecutor — successive runs can land on
            # different worker threads. The single cached connection is only ever
            # used serially (one to_thread awaited at a time, each run() wrapped
            # in its own transaction), so cross-thread reuse is safe here and
            # avoids the "SQLite objects created in a thread can only be used in
            # that same thread" ProgrammingError that aborted the aggregation.
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
        """One complete aggregation run. Synchronous, idempotent.

        Error handling: all expected I/O errors are logged, not raised. The
        caller (``run_forever``) would otherwise kill the background task and
        subsequently block voice-loop telemetry — this is explicitly excluded
        in the Plan §5-A done criteria.
        """
        try:
            daily = self._aggregate_events()
            self._upsert_daily_stats(daily.values())
            self._refresh_personal_records()
            self._set_meta("last_run_ns", str(time.time_ns()))
        except Exception:  # noqa: BLE001
            log.exception("BoardAggregator.run() abgebrochen — DB bleibt unveraendert")

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
            date = _iso_date_from_ns(ts_ns)
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
            elif event == "OpenClawTaskCompleted":
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
    # Upsert
    # ------------------------------------------------------------------

    def _upsert_daily_stats(self, rows: Iterable[DailyStats]) -> None:
        conn = self.db
        with conn:
            conn.execute("BEGIN")
            for stats in rows:
                conn.execute(
                    """
                    INSERT INTO daily_stats (
                        date, tasks_completed, tasks_failed, tools_used,
                        unique_tools_count, voice_commands_count,
                        voice_first_try_rate, hours_saved_estimate,
                        active_events_count, conversation_seconds_estimate
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(date) DO UPDATE SET
                        tasks_completed      = excluded.tasks_completed,
                        tasks_failed         = excluded.tasks_failed,
                        tools_used           = excluded.tools_used,
                        unique_tools_count   = excluded.unique_tools_count,
                        voice_commands_count = excluded.voice_commands_count,
                        voice_first_try_rate = excluded.voice_first_try_rate,
                        hours_saved_estimate = excluded.hours_saved_estimate,
                        active_events_count  = excluded.active_events_count,
                        conversation_seconds_estimate = excluded.conversation_seconds_estimate
                    """,
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
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(daily_stats)").fetchall()
        }
        if "active_events_count" not in columns:
            conn.execute(
                "ALTER TABLE daily_stats ADD COLUMN "
                "active_events_count INTEGER NOT NULL DEFAULT 0"
            )
        if "conversation_seconds_estimate" not in columns:
            conn.execute(
                "ALTER TABLE daily_stats ADD COLUMN "
                "conversation_seconds_estimate REAL NOT NULL DEFAULT 0.0"
            )


def _iso_date_from_ns(ts_ns: int) -> str:
    """Converts a nanosecond timestamp to an ISO date in the local timezone.

    Local time is the meaningful granularity for a personal dashboard — a
    commit at 00:30 belongs to the user's "today", not "yesterday in UTC".
    """
    dt = datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc).astimezone()
    return dt.strftime("%Y-%m-%d")


def _is_active_event(event: str, payload: dict[str, Any]) -> bool:
    if event not in ACTIVE_EVENT_NAMES:
        return False
    if event == "ActionExecuted":
        return bool(payload.get("success"))
    return True
