"""Automatic morning briefing — the existing task scheduler runs it, off by default.

When the person switches it on (``PUT /api/ops/morning/settings``), one
recurring task is kept in the ordinary task scheduler: a ``calendar`` trigger
(wall-clock time in the person's explicit IANA timezone, DST-correct) whose
``tool_call`` action runs :class:`MorningBriefingTool` through the
``ToolExecutor`` (AP-3). Switching it off pauses that task; switching it on
again updates and resumes the same task. There is never more than one.

The tool composes the deterministic briefing (``jarvis/ops/briefing.py``:
tasks, appointments, cancellations, moves) for the day in that timezone and
hands it to the :class:`~jarvis.ops.notify.OwnerNotifier`:

- no model call, so no cost — the phrasing step is never used here;
- delivery is SIMULATED only (``SimulatedTelegramTransport``) and still
  honours the notifier's own opt-in;
- one briefing per local day: the notifier's dedup key is the date, so a
  second firing (restart, "Run now") sends nothing twice;
- a missed slot (the app was closed at that time) is skipped, not caught up —
  the scheduler's policy for recurring work (BUG-212).

The tool lives only in the task runner's private registry, never in the
chat or router tool set (ADR-0011).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import aiosqlite

from jarvis.core.protocols import ToolResult
from jarvis.ops.briefing import BriefingComposer, normalize_language
from jarvis.ops.notify import (
    NotificationTransport,
    NotifyStore,
    OwnerNotifier,
    SimulatedTelegramTransport,
    notifications_from_briefing,
)

log = logging.getLogger(__name__)

TOOL_NAME: Final = "ops_morning_briefing"
TASK_TAG: Final = "ops:morning-briefing"
TASK_TITLE: Final = "Morning briefing"
DEFAULT_TIME: Final = "07:00"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ops_morning_settings (
    id                INTEGER PRIMARY KEY CHECK (id = 1),
    enabled           INTEGER NOT NULL DEFAULT 0,
    local_time        TEXT NOT NULL,
    timezone          TEXT NOT NULL,
    language          TEXT,
    include_calendar  INTEGER NOT NULL DEFAULT 1,
    updated_ms        INTEGER NOT NULL
)
"""


@dataclass(frozen=True, slots=True)
class MorningSettings:
    enabled: bool = False
    local_time: str = DEFAULT_TIME
    timezone: str = ""
    language: str | None = None
    include_calendar: bool = True
    updated_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "local_time": self.local_time,
            "timezone": self.timezone,
            "language": self.language,
            "include_calendar": self.include_calendar,
            "delivery": "simulated",
            "updated_ms": self.updated_ms,
        }


def validate_settings(settings: MorningSettings) -> MorningSettings:
    """Raise ``ValueError`` for a time or timezone the scheduler would refuse."""
    from jarvis.tasks.schema import TriggerCalendar

    if settings.enabled or settings.timezone:
        TriggerCalendar(timezone=settings.timezone, local_time=settings.local_time)
    if settings.language is not None:
        normalize_language(settings.language)
    return settings


class MorningStore:
    """The morning-briefing switch, in the Ops core's own SQLite file."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)

    async def _connect(self) -> aiosqlite.Connection:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(self._db_path)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA busy_timeout = 5000")
        await conn.execute(_SCHEMA)
        return conn

    async def settings(self) -> MorningSettings:
        conn = await self._connect()
        try:
            cur = await conn.execute("SELECT * FROM ops_morning_settings WHERE id = 1")
            row = await cur.fetchone()
        finally:
            await conn.close()
        if row is None:
            return MorningSettings()
        return MorningSettings(
            enabled=bool(row["enabled"]),
            local_time=str(row["local_time"]),
            timezone=str(row["timezone"]),
            language=row["language"],
            include_calendar=bool(row["include_calendar"]),
            updated_ms=int(row["updated_ms"]),
        )

    async def save(self, settings: MorningSettings) -> MorningSettings:
        settings = validate_settings(settings)
        stored = MorningSettings(
            settings.enabled,
            settings.local_time,
            settings.timezone,
            settings.language,
            settings.include_calendar,
            int(time.time() * 1000),
        )
        conn = await self._connect()
        try:
            await conn.execute(
                "INSERT INTO ops_morning_settings (id, enabled, local_time, timezone, language,"
                " include_calendar, updated_ms) VALUES (1, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT (id) DO UPDATE SET enabled = excluded.enabled,"
                " local_time = excluded.local_time, timezone = excluded.timezone,"
                " language = excluded.language, include_calendar = excluded.include_calendar,"
                " updated_ms = excluded.updated_ms",
                (
                    int(stored.enabled),
                    stored.local_time,
                    stored.timezone,
                    stored.language,
                    int(stored.include_calendar),
                    stored.updated_ms,
                ),
            )
            await conn.commit()
        finally:
            await conn.close()
        return stored


# ---------------------------------------------------------------- schedule


def task_spec(settings: MorningSettings, task_id: str | None = None) -> Any:
    """The one recurring task the scheduler keeps for the morning briefing.

    *task_id* keeps an existing task's identity when its schedule is replaced.
    """
    from uuid import UUID

    from jarvis.tasks.schema import TaskSpec, ToolCallAction, TriggerCalendar

    identity: dict[str, Any] = {"id": UUID(task_id)} if task_id else {}
    return TaskSpec(
        **identity,
        title=TASK_TITLE,
        trigger=TriggerCalendar(timezone=settings.timezone, local_time=settings.local_time),
        action=ToolCallAction(
            tool_name=TOOL_NAME,
            args={
                "timezone": settings.timezone,
                "language": settings.language,
                "include_calendar": settings.include_calendar,
            },
        ),
        tags=(TASK_TAG,),
    )


async def _tagged_tasks(store: Any) -> list[dict[str, Any]]:
    import json

    from jarvis.tasks.schema import TASK_STATES, TERMINAL_STATES

    open_states = [s for s in TASK_STATES if s not in TERMINAL_STATES]
    rows = await store.list(open_states, limit=1000)
    tagged = []
    for row in rows:
        try:
            tags = json.loads(row.get("spec_json") or "{}").get("tags") or []
        except ValueError:  # a malformed row is not ours to manage
            continue
        if TASK_TAG in tags:
            tagged.append(row)
    tagged.sort(key=lambda r: int(r.get("created_at_ns") or 0))
    return tagged


async def apply_schedule(settings: MorningSettings, *, scheduler: Any, store: Any) -> str | None:
    """Make the scheduler match *settings*. Returns the task id, if one exists.

    On: update and resume the one tagged task, or create it. Off: pause it.
    Extra tagged tasks (should never exist) are cancelled so the briefing can
    never be scheduled twice.
    """
    tasks = await _tagged_tasks(store)
    keep = tasks[0] if tasks else None
    for extra in tasks[1:]:
        await scheduler.cancel_task(str(extra["id"]), reason="duplicate_morning_briefing")
    if not settings.enabled:
        if keep is not None and keep["state"] != "paused":
            await scheduler.pause(str(keep["id"]))
        return str(keep["id"]) if keep is not None else None
    if keep is None:
        return str(await scheduler.schedule(task_spec(settings)))
    task_id = str(keep["id"])
    await scheduler.update_task(task_id, task_spec(settings, task_id))
    current = await store.get(task_id)
    if current is not None and current["state"] == "paused":
        await scheduler.resume(task_id)
    return task_id


# ---------------------------------------------------------------- tool


class MorningBriefingTool:
    """Compose today's briefing and hand it to the notifier (simulated send)."""

    name = TOOL_NAME
    description = (
        "Internal: compose the person's morning briefing and deliver it through the "
        "notifier (simulated). Run by the task scheduler only."
    )
    risk_tier = "monitor"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "timezone": {"type": "string"},
            "language": {"type": ["string", "null"]},
            "include_calendar": {"type": "boolean"},
        },
        "required": ["timezone"],
    }

    def __init__(
        self,
        *,
        composer: Callable[[], BriefingComposer | None],
        notify_store: Callable[[], NotifyStore | None],
        transport: Callable[[], NotificationTransport] = SimulatedTelegramTransport,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._composer = composer
        self._notify_store = notify_store
        self._transport = transport
        self._clock = clock

    @property
    def schema(self) -> dict[str, Any]:
        return self.parameters

    async def execute(self, args: dict[str, Any], ctx: Any = None) -> ToolResult:
        from jarvis.tasks.calendar import calendar_zone

        try:
            zone = calendar_zone(str(args.get("timezone") or ""))
        except ValueError:  # reported to the run as a code; the schedule stays on
            return ToolResult(False, None, "invalid_timezone")
        composer = self._composer()
        store = self._notify_store()
        if composer is None or store is None:
            return ToolResult(False, None, "ops_core_unavailable")
        now = self._clock().astimezone(zone)
        try:
            briefing = await composer.compose(
                now=now,
                language=str(args.get("language") or "en"),
                include_calendar=bool(args.get("include_calendar", True)),
            )
            transport = self._transport()
            report = await OwnerNotifier(store, transport).deliver(
                notifications_from_briefing(briefing)
            )
        except Exception as exc:  # noqa: BLE001 — the run fails visibly, the schedule stays
            log.warning("ops morning briefing failed (%s)", type(exc).__name__, exc_info=True)
            return ToolResult(False, None, f"morning_briefing_failed:{type(exc).__name__}")
        counts = report.to_dict()["counts"]
        delivered = report.count("simulated") + report.count("delivered")
        log.info(
            "ops morning briefing %s: %d delivered (%s)",
            briefing.day.isoformat(),
            delivered,
            counts,
        )
        return ToolResult(
            True,
            {
                "day": briefing.day.isoformat(),
                "timezone": str(args.get("timezone")),
                "delivered": delivered,
                "counts": counts,
                "simulated": bool(getattr(transport, "simulated", True)),
            },
        )


def ops_task_tools(
    *,
    composer: Callable[[], BriefingComposer | None],
    notify_store: Callable[[], NotifyStore | None],
) -> dict[str, Any]:
    """The task runner's private tool registry: the Ops core's scheduled tools."""
    return {TOOL_NAME: MorningBriefingTool(composer=composer, notify_store=notify_store)}


__all__ = [
    "DEFAULT_TIME",
    "TASK_TAG",
    "TASK_TITLE",
    "TOOL_NAME",
    "MorningBriefingTool",
    "MorningSettings",
    "MorningStore",
    "apply_schedule",
    "ops_task_tools",
    "task_spec",
    "validate_settings",
]
