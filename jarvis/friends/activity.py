"""ActivityLedger — the count-only sidecar a usage pulse is built from.

Why a dedicated ledger
----------------------
"How much does my friend use Personal Jarvis" needs a number that survives
retention. Every store that already holds this information holds it *with
content attached*: the session database keeps transcripts, the mission store
keeps titles, the dictation history keeps the dictated text. Deriving a shared
usage figure from any of them means walking rows that must never be shared, in
a process that will one day be asked to serialise what it just read.

So the shareable figures live in their own file, ``socials_activity.json``,
and that file physically cannot leak anything else: it stores four integers
per local calendar day and nothing else. No titles, no ids, no text, no paths.
The privacy guarantee is a property of the storage shape, not of a filter that
someone has to remember to apply.

Two details inherited from :mod:`jarvis.dictation.stats`
--------------------------------------------------------
1. **Days are LOCAL days**, because "did I use it today" is a wall-clock
   question. Bucketing by UTC shifts everyone's evening onto tomorrow.
2. **Today gets grace** in the streak — a quiet morning does not read as 0.

Both helpers are imported from that module rather than re-implemented; a second
copy of the streak rule would drift from the first one within a release.

Every method degrades to "no activity" instead of raising. A counter is never
worth costing someone a voice session.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jarvis.dictation.stats import (
    current_streak,
    local_day,
    longest_streak,
    today_key,
)

log = logging.getLogger(__name__)

#: The only metrics this ledger knows. A fixed tuple rather than free-form keys:
#: an open key space is how a "just this once" label with a project name in it
#: ends up in a file that is designed to be shareable.
METRICS: tuple[str, ...] = (
    "voice_sessions",
    "missions",
    "agent_tasks",
    "messages_sent",
)

#: Upper bound on retained day buckets (~10 years at four ints a day).
MAX_DAYS_RETAINED = 3_650

#: Bus event names mapped onto a metric. Anything not listed is ignored, so a
#: new event type can never silently start feeding the shared figures.
EVENT_METRICS: dict[str, str] = {
    "VoiceSessionStarted": "voice_sessions",
    "MissionCompleted": "missions",
    "JarvisAgentTaskCompleted": "agent_tasks",
}


def _now_ns() -> int:
    return time.time_ns()


@dataclass(frozen=True, slots=True)
class ActivityDay:
    """One local calendar day of counts. Integers only, by construction."""

    date: str
    voice_sessions: int = 0
    missions: int = 0
    agent_tasks: int = 0
    messages_sent: int = 0

    @property
    def total(self) -> int:
        return (
            self.voice_sessions
            + self.missions
            + self.agent_tasks
            + self.messages_sent
        )

    def to_dict(self) -> dict[str, int]:
        return {
            "voice_sessions": self.voice_sessions,
            "missions": self.missions,
            "agent_tasks": self.agent_tasks,
            "messages_sent": self.messages_sent,
        }

    def plus(self, metric: str, count: int) -> ActivityDay:
        values = self.to_dict()
        values[metric] = values.get(metric, 0) + count
        return ActivityDay(date=self.date, **values)


def default_activity_path() -> Path:
    """``<user data>/data/socials_activity.json``."""
    from jarvis.core.paths import user_data_dir

    return Path(user_data_dir()) / "data" / "socials_activity.json"


class ActivityLedger:
    """Per-day counts of the things a friend may be told about.

    Reads and writes are atomic (tempfile + ``os.replace``); a crash mid-write
    leaves the previous file intact rather than a truncated one.
    """

    def __init__(self, path: Path | str | None = None) -> None:
        self._path = Path(path) if path is not None else default_activity_path()
        # Shared per path, like DictationStats: several callers construct their
        # own ledger for the same file, and a per-object lock would leave the
        # read-modify-write cycle unguarded between them.
        from jarvis.dictation._locks import store_lock

        self._lock = store_lock(self._path)

    @property
    def path(self) -> Path:
        return self._path

    # -- reading ---------------------------------------------------------

    def load(self) -> tuple[dict[str, ActivityDay], int]:
        """``(days, last_active_ns)``. Unreadable or corrupt reads as empty."""
        days: dict[str, ActivityDay] = {}
        last_active = 0
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return days, last_active
        except OSError as exc:
            log.warning("socials activity ledger unreadable: %s", exc)
            return days, last_active
        try:
            payload = json.loads(raw)
        except (ValueError, TypeError) as exc:
            log.warning("socials activity ledger is corrupt, ignoring it: %s", exc)
            return days, last_active
        if not isinstance(payload, dict):
            return days, last_active

        last_active = _as_int(payload.get("last_active_ns"))
        stored = payload.get("days")
        if isinstance(stored, dict):
            for key, value in stored.items():
                if not isinstance(value, dict):
                    continue  # one bad bucket never invalidates the rest
                days[str(key)] = ActivityDay(
                    date=str(key),
                    **{m: _as_int(value.get(m)) for m in METRICS},
                )
        return days, last_active

    def active_days(self) -> list[str]:
        """Local days with at least one recorded event, newest first."""
        days, _ = self.load()
        return sorted(
            (d.date for d in days.values() if d.total > 0), reverse=True
        )

    def streaks(self) -> tuple[int, int]:
        """``(current_days, longest_days)`` over the recorded days."""
        active = self.active_days()
        return current_streak(active), longest_streak(active)

    def last_active_ns(self) -> int:
        _, last = self.load()
        return last

    # -- writing ---------------------------------------------------------

    def record(
        self,
        metric: str,
        *,
        at_ns: int | None = None,
        count: int = 1,
    ) -> bool:
        """Count one event. ``False`` when nothing was recorded.

        An unknown metric is dropped rather than stored — see :data:`METRICS`.
        """
        if metric not in METRICS:
            log.debug("socials activity: unknown metric %r ignored", metric)
            return False
        amount = _as_int(count)
        if amount <= 0:
            return False
        moment = _as_int(at_ns) or _now_ns()
        day = _day_key_for(moment)
        try:
            with self._lock:
                days, last_active = self.load()
                previous = days.get(day, ActivityDay(date=day))
                days[day] = previous.plus(metric, amount)
                self._write(days, max(last_active, moment))
        except Exception:  # noqa: BLE001 — a counter never costs a session
            log.warning("could not record socials activity", exc_info=True)
            return False
        return True

    def reset(self) -> bool:
        """Zero every count. What "stop sharing my usage" also calls."""
        try:
            with self._lock:
                self._write({}, 0)
                return True
        except Exception:  # noqa: BLE001
            log.warning("could not reset the socials activity ledger", exc_info=True)
            return False

    def _write(self, days: dict[str, ActivityDay], last_active_ns: int) -> None:
        ordered = sorted(days.values(), key=lambda d: d.date, reverse=True)
        kept = ordered[:MAX_DAYS_RETAINED]
        payload = {
            "version": 1,
            "last_active_ns": _as_int(last_active_ns),
            "days": {d.date: d.to_dict() for d in kept},
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(self._path.parent), prefix=".socials_activity_", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
            os.replace(tmp_name, self._path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise


class ActivityRecorder:
    """Bus observer that feeds the ledger. Lifecycle mirrors StatusPublisher.

    Only the event names in :data:`EVENT_METRICS` are counted and only their
    *arrival* is — no field of any event is ever read, so a future event
    carrying a title or a transcript cannot start leaking through this path.
    """

    def __init__(self, bus: Any, ledger: ActivityLedger | None = None) -> None:
        self._bus = bus
        self._ledger = ledger or ActivityLedger()
        self._handler: Any = None
        self._started = False

    @property
    def ledger(self) -> ActivityLedger:
        return self._ledger

    async def start(self) -> None:
        if self._started:
            return
        self._handler = self._on_event
        self._bus.subscribe_all(self._handler)
        self._started = True
        log.info("socials ActivityRecorder started")

    async def stop(self) -> None:
        if not self._started:
            return
        if self._handler is not None:
            unsubscribe = getattr(self._bus, "unsubscribe_all", None)
            if unsubscribe is not None:
                unsubscribe(self._handler)
            self._handler = None
        self._started = False
        log.info("socials ActivityRecorder stopped")

    async def _on_event(self, event: Any) -> None:
        metric = EVENT_METRICS.get(type(event).__name__)
        if metric is None:
            return
        timestamp = _as_int(getattr(event, "timestamp_ns", 0)) or _now_ns()
        # The write is small and rare (a handful of events a day), but the bus
        # dispatches wildcard handlers under a hard timeout — so it runs off the
        # event loop rather than blocking it on a disk round-trip.
        import asyncio

        await asyncio.to_thread(self._ledger.record, metric, at_ns=timestamp)


def _day_key_for(moment_ns: int) -> str:
    """Nanosecond timestamp -> its LOCAL calendar day."""
    from datetime import UTC, datetime

    try:
        stamp = datetime.fromtimestamp(moment_ns / 1_000_000_000, tz=UTC)
    except (OSError, OverflowError, ValueError, TypeError):
        return today_key()
    return local_day(stamp.isoformat()) or today_key()


def daily_totals(days: Iterable[ActivityDay]) -> int:
    return sum(d.total for d in days)


def _as_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


__all__ = [
    "EVENT_METRICS",
    "MAX_DAYS_RETAINED",
    "METRICS",
    "ActivityDay",
    "ActivityLedger",
    "ActivityRecorder",
    "daily_totals",
    "default_activity_path",
]
