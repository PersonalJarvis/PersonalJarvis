"""BoardStore — read-only API facade on top of ``personal.db``.

The aggregator writes; the store reads. It is used by the FastAPI routes
(``jarvis/ui/web/board_routes.py``) to produce JSON-serialisable dicts.
No event-parsing logic here — this class only exposes aggregated data.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date as _date
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


class BoardStore:
    """Synchronous read interface for the board DB."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)

    # ------------------------------------------------------------------
    # Connection-Handling
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        """Short-lived connection. SQLite is in WAL mode — readers do not block
        writers, so no connection-pool complexity is needed.
        """
        if not self._db_path.exists():
            # Create an empty DB so that GET endpoints do not raise 500 errors
            # before the first aggregator run.
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self._db_path, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.executescript(
                (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
            )
            _ensure_schema(conn)
            return conn
        conn = sqlite3.connect(self._db_path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        _ensure_schema(conn)
        return conn

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def summary(self, *, window_days: int = 30) -> dict[str, Any]:
        """Totals and windowed stats. No PII.

        ``window_days`` controls the time range over which ``tools_recent``
        and ``voice_first_try_rate`` are averaged. Totals always cover the
        entire history.
        """
        conn = self._connect()
        try:
            conn.row_factory = sqlite3.Row
            cutoff = (_today() - timedelta(days=window_days)).isoformat()

            totals = conn.execute(
                """
                SELECT
                    COALESCE(SUM(tasks_completed), 0)      AS tasks_completed,
                    COALESCE(SUM(tasks_failed), 0)         AS tasks_failed,
                    COALESCE(SUM(voice_commands_count), 0) AS voice_commands,
                    COALESCE(SUM(hours_saved_estimate), 0) AS hours_saved,
                    COALESCE(SUM(active_events_count), 0)  AS activity_events,
                    COALESCE(SUM(conversation_seconds_estimate), 0)
                                                             AS conversation_seconds,
                    COALESCE(SUM(CASE WHEN active_events_count > 0 THEN 1 ELSE 0 END), 0)
                                                             AS active_days,
                    MIN(date)                              AS first_day
                FROM daily_stats
                """
            ).fetchone()

            window = conn.execute(
                """
                SELECT
                    COALESCE(SUM(tasks_completed), 0)      AS tasks_completed,
                    COALESCE(SUM(tasks_failed), 0)         AS tasks_failed,
                    COALESCE(SUM(voice_commands_count), 0) AS voice_commands,
                    COALESCE(SUM(hours_saved_estimate), 0) AS hours_saved,
                    COALESCE(SUM(active_events_count), 0)  AS activity_events,
                    COALESCE(SUM(conversation_seconds_estimate), 0)
                                                             AS conversation_seconds,
                    AVG(voice_first_try_rate)              AS avg_first_try
                FROM daily_stats
                WHERE date >= ?
                """,
                (cutoff,),
            ).fetchone()

            tool_rows = conn.execute(
                "SELECT tools_used FROM daily_stats WHERE date >= ?",
                (cutoff,),
            ).fetchall()
            tools_recent: set[str] = set()
            for row in tool_rows:
                try:
                    for name in json.loads(row["tools_used"] or "[]"):
                        if isinstance(name, str):
                            tools_recent.add(name)
                except (TypeError, json.JSONDecodeError):
                    continue

            streak_days = _calc_streak(conn)

            return {
                "window_days": window_days,
                "totals": {
                    "tasks_completed": int(totals["tasks_completed"]),
                    "tasks_failed":    int(totals["tasks_failed"]),
                    "voice_commands":  int(totals["voice_commands"]),
                    "hours_saved":     float(totals["hours_saved"]),
                    "activity_events": int(totals["activity_events"]),
                    "conversation_hours": (
                        float(totals["conversation_seconds"]) / 3600.0
                    ),
                    "active_days":     int(totals["active_days"]),
                    "first_day":       totals["first_day"],
                },
                "window": {
                    "tasks_completed":   int(window["tasks_completed"]),
                    "tasks_failed":      int(window["tasks_failed"]),
                    "voice_commands":    int(window["voice_commands"]),
                    "hours_saved":       float(window["hours_saved"]),
                    "activity_events":   int(window["activity_events"]),
                    "conversation_hours": (
                        float(window["conversation_seconds"]) / 3600.0
                    ),
                    "voice_first_try_rate": (
                        float(window["avg_first_try"])
                        if window["avg_first_try"] is not None else None
                    ),
                    "unique_tools": len(tools_recent),
                },
                "streak_days": streak_days,
            }
        finally:
            conn.close()

    def heatmap(self, *, days: int = 365) -> dict[str, Any]:
        """Per-day activity for the last ``days`` days.

        Retrieves GitHub-style contribution-grid data. Each cell contains
        only the ``tasks_completed`` total — deliberately **not** a streak
        marker (plan §0: no breakable streaks).
        """
        conn = self._connect()
        try:
            conn.row_factory = sqlite3.Row
            end = _today()
            start = end - timedelta(days=days - 1)
            rows = conn.execute(
                "SELECT date, tasks_completed, tasks_failed, active_events_count, "
                "conversation_seconds_estimate "
                "FROM daily_stats WHERE date >= ? ORDER BY date",
                (start.isoformat(),),
            ).fetchall()
            index = {
                row["date"]: (
                    int(row["tasks_completed"]),
                    int(row["tasks_failed"]),
                    int(row["active_events_count"]),
                    float(row["conversation_seconds_estimate"]) / 3600.0,
                )
                for row in rows
            }
            cells = []
            cursor = start
            while cursor <= end:
                iso = cursor.isoformat()
                completed, failed, activity_events, conversation_hours = index.get(
                    iso,
                    (0, 0, 0, 0.0),
                )
                cells.append({
                    "date": iso,
                    "tasks_completed": completed,
                    "tasks_failed": failed,
                    "activity_events": activity_events,
                    "conversation_hours": conversation_hours,
                })
                cursor += timedelta(days=1)
            return {
                "start": start.isoformat(),
                "end": end.isoformat(),
                "days": len(cells),
                "cells": cells,
            }
        finally:
            conn.close()

    def tools(self, *, window_days: int = 90) -> dict[str, Any]:
        """Histogram of tools used in the last ``window_days`` days."""
        conn = self._connect()
        try:
            conn.row_factory = sqlite3.Row
            cutoff = (_today() - timedelta(days=window_days)).isoformat()
            rows = conn.execute(
                "SELECT tools_used FROM daily_stats WHERE date >= ?",
                (cutoff,),
            ).fetchall()
            counts: dict[str, int] = {}
            for row in rows:
                try:
                    tools = json.loads(row["tools_used"] or "[]")
                except (TypeError, json.JSONDecodeError):
                    continue
                for name in tools:
                    if not isinstance(name, str):
                        continue
                    counts[name] = counts.get(name, 0) + 1
            histogram = sorted(
                ({"tool": k, "days_used": v} for k, v in counts.items()),
                key=lambda d: (-d["days_used"], d["tool"]),
            )
            return {
                "window_days": window_days,
                "total_unique": len(histogram),
                "histogram": histogram,
            }
        finally:
            conn.close()

    def records(self) -> dict[str, Any]:
        conn = self._connect()
        try:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT metric, value, achieved_on, context FROM personal_records "
                "ORDER BY metric"
            ).fetchall()
            records = []
            for row in rows:
                try:
                    context = json.loads(row["context"] or "{}")
                except (TypeError, json.JSONDecodeError):
                    context = {}
                records.append({
                    "metric": row["metric"],
                    "value": float(row["value"]),
                    "achieved_on": row["achieved_on"],
                    "context": context,
                })
            return {"records": records}
        finally:
            conn.close()

    def days_observed(self) -> int:
        """Days since the first day with activity (active_events_count > 0).

        Passed to the prompt by the BioGenerator as ``days_observed`` and
        used by the BioScheduler as the cold-start threshold. Returns 0 for
        an empty DB (no cold-start fires).
        """
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT MIN(date) AS first_active FROM daily_stats "
                "WHERE active_events_count > 0"
            ).fetchone()
            first = row["first_active"] if row is not None else None
            if not first:
                return 0
            try:
                first_date = datetime.fromisoformat(first).date()
            except ValueError:
                return 0
            delta = (_today() - first_date).days
            return max(0, int(delta))
        finally:
            conn.close()


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _today() -> _date:
    return datetime.now().astimezone().date()


def _calc_streak(conn: sqlite3.Connection) -> int:
    """Running count of consecutive days up to today with ``active_events_count > 0``.

    *Not* a streak in the Snapchat sense — no push notification, no UI nag,
    no "You lost your streak!" pop-up. Plan §0 forbids breakable streaks.
    This number is rendered in the UI only as an info badge ("5-day series")
    and stays at 0 when the user skips a day.
    """
    cursor = _today()
    streak = 0
    while True:
        row = conn.execute(
            "SELECT active_events_count FROM daily_stats WHERE date = ?",
            (cursor.isoformat(),),
        ).fetchone()
        if row is None or int(row["active_events_count"]) <= 0:
            break
        streak += 1
        cursor -= timedelta(days=1)
    return streak


def _ensure_schema(conn: sqlite3.Connection) -> None:
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
