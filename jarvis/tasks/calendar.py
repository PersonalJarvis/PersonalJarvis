"""Portable calendar arithmetic. Missing clock times skip; folds fire once."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

if TYPE_CHECKING:
    from .schema import TriggerCalendar


def calendar_zone(name: str) -> ZoneInfo:
    """Validate the same IANA capability on Windows, macOS and headless Linux."""
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"Unknown IANA timezone: {name!r}") from exc


def absolute_timestamp(value: str, timezone: str | None) -> str:
    """Resolve a chat's one-shot clock time without consulting the server zone."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is not None:
        return parsed.isoformat()
    if not timezone:
        raise ValueError("A local timestamp requires the user's IANA timezone or a UTC offset")
    zone = calendar_zone(timezone)
    local = parsed.replace(tzinfo=zone)
    if local.astimezone(UTC).astimezone(zone).replace(tzinfo=None) != parsed:
        raise ValueError("That local time does not exist because the clock moves forward")
    if local.utcoffset() != local.replace(fold=1).utcoffset():
        raise ValueError("That local time occurs twice; provide an explicit UTC offset")
    return local.isoformat()


def next_calendar_due_ns(trigger: TriggerCalendar, now_ns: int) -> int:
    """Find the next local calendar slot, strictly after the given UTC instant.

    A nonexistent spring-forward time is skipped. A repeated autumn time uses
    its first occurrence only, including when resumed during the second fold.
    Invalid month dates (e.g. February 31) are skipped, never shifted.
    """
    zone = calendar_zone(trigger.timezone)
    day = datetime.fromtimestamp(now_ns / 1e9, UTC).astimezone(zone).date()
    if trigger.start_date:
        day = max(day, date.fromisoformat(trigger.start_date))
    clock = time.fromisoformat(trigger.local_time)
    # Gregorian dates and weekdays repeat every 400 years. This also gives
    # sparse leap-day + weekday combinations a finite, honest search bound.
    for offset in range(146_097):
        candidate_day = day + timedelta(days=offset)
        if trigger.weekdays and candidate_day.weekday() not in trigger.weekdays:
            continue
        if trigger.month_days and candidate_day.day not in trigger.month_days:
            continue
        if trigger.months and candidate_day.month not in trigger.months:
            continue
        local = datetime.combine(candidate_day, clock, zone)
        utc = local.astimezone(UTC)
        if utc.astimezone(zone).replace(tzinfo=None) != local.replace(tzinfo=None):
            continue
        due = int(utc.timestamp()) * 1_000_000_000
        if due > now_ns:
            return due
    raise ValueError("Calendar rule has no matching date")
