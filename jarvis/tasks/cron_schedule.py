"""Five-field cron using Jarvis' existing croniter dependency and IANA rules."""

from datetime import UTC, datetime

from .calendar import calendar_zone


def validate_cron(expression: str, timezone: str) -> None:
    from croniter import croniter  # type: ignore[import-untyped]

    calendar_zone(timezone)
    if len(expression.split()) != 5 or not croniter.is_valid(expression):
        raise ValueError("Use a valid five-field cron expression")


def next_cron_ns(expression: str, timezone: str, now_ns: int) -> int:
    from croniter import croniter  # type: ignore[import-untyped]

    zone = calendar_zone(timezone)
    local = datetime.fromtimestamp(now_ns / 1e9, UTC).astimezone(zone)
    iterator = croniter(expression, local.replace(tzinfo=None))
    for _ in range(180):
        candidate = iterator.get_next(datetime).replace(tzinfo=zone, fold=0)
        utc = candidate.astimezone(UTC)
        if utc.astimezone(zone).replace(tzinfo=None) != candidate.replace(tzinfo=None):
            continue  # A nonexistent spring-forward minute is skipped.
        due = int(utc.timestamp()) * 1_000_000_000
        if due > now_ns:
            return due
    raise ValueError("No valid cron occurrence could be resolved")
