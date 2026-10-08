"""Today's calendar for the Ops core: upcoming, cancelled, moved — facts only.

Reads the person's day through the ``google_calendar`` tool run by the
``ToolExecutor`` (AP-3, read-only ``list_events`` with ``show_deleted``) and
sorts every event into what the briefing and the notifier need:

- **upcoming** — confirmed or tentative events of the day that are not over.
  A cancelled event is never listed here.
- **cancelled** — events Google reports with ``status: cancelled``. One that
  carries no start time cannot be placed on a day and is left out.
- **moved** — only when Google states the original slot (``original_start``,
  set for a moved instance of a recurring series) and it differs from the
  current start. A single event has no change history in the API, so its
  earlier time is unknown and is never guessed.
- **short notice** — a cancellation or move whose change time (Google's
  ``updated``) is known and falls within :data:`SHORT_NOTICE_HOURS` before
  the original appointment. Without a change time nothing is marked.

Only a status leaves the reader on failure, never a provider error text
(AP-34). Pure classification functions; the reader is the only I/O.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Final, Protocol
from uuid import uuid4

log = logging.getLogger(__name__)

CALENDAR_TOOL: Final = "google_calendar"
SHORT_NOTICE_HOURS: Final = 24
MAX_EVENTS: Final = 50
READ_TIMEOUT_S: Final = 45.0
_DAY = timedelta(days=1)


@dataclass(frozen=True, slots=True)
class CalendarDay:
    """Today's events, or why there are none.

    ``status``: ``ok`` / ``empty`` / ``not_connected`` / ``unavailable`` /
    ``skipped``. Never carries a provider error text (AP-34).
    """

    status: str
    events: tuple[dict[str, Any], ...] = ()
    cancelled: tuple[dict[str, Any], ...] = ()


class CalendarReader(Protocol):
    async def read_day(self, day: date, now: datetime) -> CalendarDay: ...


def _parse(value: Any, tz: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        if len(value) == 10:  # all-day: a bare date
            return datetime.combine(date.fromisoformat(value), time.min, tzinfo=tz)
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:  # an unreadable time is left out, not guessed
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=tz)


def _is_date(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 10


def _clean(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _clock(moment: datetime, tz: Any, all_day: bool) -> str:
    return "" if all_day else moment.astimezone(tz).strftime("%H:%M")


def _short_notice(changed_at: datetime | None, reference: datetime) -> bool:
    if changed_at is None:
        return False
    return changed_at >= reference - timedelta(hours=SHORT_NOTICE_HOURS)


def classify_day(
    day: date, events: Sequence[Mapping[str, Any]], *, tz: Any, now: datetime
) -> tuple[tuple[dict[str, Any], ...], tuple[dict[str, Any], ...]]:
    """``(upcoming, cancelled)`` for *day* (local), each sorted all-day first,
    then by start. Pure: same events, same answer."""
    day_start = datetime.combine(day, time.min, tzinfo=tz)
    day_end = day_start + _DAY
    upcoming: list[tuple[bool, datetime, str, dict[str, Any]]] = []
    cancelled: list[tuple[bool, datetime, str, dict[str, Any]]] = []
    seen: set[tuple[str, str]] = set()
    for event in events:
        state = str(event.get("status") or "confirmed")
        start = _parse(event.get("start"), tz)
        original = _parse(event.get("original_start"), tz)
        changed_at = _parse(event.get("updated"), tz)
        # A cancelled instance of a series may only carry its original slot.
        anchor = start if start is not None else (original if state == "cancelled" else None)
        if anchor is None:
            continue
        finish = _parse(event.get("end"), tz) or anchor
        finish = max(anchor, finish)
        all_day = _is_date(event.get("start")) or (
            start is None and _is_date(event.get("original_start"))
        )
        if not (anchor < day_end and (finish > day_start or anchor >= day_start)):
            continue
        event_id = _clean(event.get("id"), 200)
        key = (event_id or _clean(event.get("summary"), 160), anchor.isoformat())
        if key in seen:
            continue
        seen.add(key)
        moved_from = None
        if state != "cancelled" and original is not None and original != anchor:
            moved_from = {
                "start": event.get("original_start"),
                "time": _clock(original, tz, _is_date(event.get("original_start"))),
                "day": original.astimezone(tz).date().isoformat(),
            }
        reference = original if original is not None else anchor
        changed = state == "cancelled" or moved_from is not None
        entry = {
            "id": event_id,
            "title": _clean(event.get("summary"), 160) or "(no title)",
            "start": event.get("start") or event.get("original_start"),
            "end": event.get("end"),
            "all_day": all_day,
            "time": _clock(anchor, tz, all_day),
            "location": _clean(event.get("location"), 120),
            "state": state,
            "moved_from": moved_from,
            "changed_at": event.get("updated") if changed else None,
            "short_notice": changed and _short_notice(changed_at, reference),
        }
        row = (not all_day, anchor, event_id, entry)
        if state == "cancelled":
            cancelled.append(row)
        elif all_day or finish > now:
            upcoming.append(row)
    upcoming.sort(key=lambda r: r[:3])
    cancelled.sort(key=lambda r: r[:3])
    return tuple(r[3] for r in upcoming), tuple(r[3] for r in cancelled)


class ToolCalendarReader:
    """Today's events through the ``google_calendar`` tool and the
    ``ToolExecutor`` (AP-3): one read-only ``list_events`` call."""

    def __init__(
        self,
        tools: Callable[[], Mapping[str, Any] | None],
        executor: Callable[[], Any],
    ) -> None:
        self._tools = tools
        self._executor = executor

    async def read_day(self, day: date, now: datetime) -> CalendarDay:
        tool = (self._tools() or {}).get(CALENDAR_TOOL)
        executor = self._executor()
        if tool is None or executor is None:
            return CalendarDay("not_connected")
        day_start = datetime.combine(day, time.min, tzinfo=now.tzinfo)
        args = {
            "action": "list_events",
            "time_min": day_start.isoformat(),
            "time_max": (day_start + _DAY).isoformat(),
            "max_results": MAX_EVENTS,
            "show_deleted": True,
        }
        try:
            result = await asyncio.wait_for(
                executor.execute(
                    tool,
                    args,
                    user_utterance="ops briefing: today's calendar",
                    trace_id=uuid4(),
                    rationale="read-only calendar facts for the daily briefing",
                ),
                timeout=READ_TIMEOUT_S,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — a dead calendar must not sink the briefing
            # The type only: a provider message may carry account details (AP-34).
            log.warning("ops calendar: read failed (%s)", type(exc).__name__)
            return CalendarDay("unavailable")
        if not bool(getattr(result, "success", False)):
            log.warning("ops calendar: the calendar tool reported a failed read")
            return CalendarDay("unavailable")
        output = getattr(result, "output", None)
        raw = output.get("events") if isinstance(output, Mapping) else None
        events = [e for e in (raw or []) if isinstance(e, Mapping)]
        upcoming, cancelled = classify_day(day, events, tz=now.tzinfo, now=now)
        status = "ok" if upcoming or cancelled else "empty"
        return CalendarDay(status, upcoming, cancelled)


__all__ = [
    "CALENDAR_TOOL",
    "SHORT_NOTICE_HOURS",
    "CalendarDay",
    "CalendarReader",
    "ToolCalendarReader",
    "classify_day",
]
