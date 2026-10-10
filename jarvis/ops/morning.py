"""Daily morning schedule — the existing task scheduler runs it, off by default.

Two moments a day, both in the person's explicit IANA timezone (wall-clock,
DST-correct), each ONE recurring task in the ordinary task scheduler whose
``tool_call`` action runs :class:`MorningBriefingTool` through the
``ToolExecutor`` (AP-3):

- **prepare** (default 06:30) — composes the morning briefing and stores it
  (:class:`BriefingSnapshotStore`). Nothing is said or sent: the briefing
  stands ready for the person to ask for ("what's on today?"). A voice
  question is answered live; when the calendar cannot be read at that
  moment, this prepared version answers instead, with its time.
- **overview** (default 07:00) — reads the calendar again (changes since
  06:30 count) and sends the day's overview through the
  :class:`~jarvis.ops.notify.OwnerNotifier` — whether or not the briefing
  was already heard by voice. Its dedup key is the day AND the slot, so a
  second firing (restart, "Run now") of the same slot sends nothing twice.

Both: no model call, so no cost; delivery is simulated unless the owner's
live switch is on (``jarvis/ops/delivery.py``) and the notifier's opt-in
still applies; a missed slot is skipped, not caught up (BUG-212).

Switching the schedule off pauses both tasks; on again updates and resumes
the same tasks. There is never more than one per moment. The tool lives
only in the task runner's private registry, never in the chat or router
tool set (ADR-0011).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Final

import aiosqlite

from jarvis.core.protocols import ToolResult
from jarvis.ops.briefing import Briefing, BriefingComposer, normalize_language
from jarvis.ops.notify import (
    NotificationTransport,
    NotifySettings,
    NotifyStore,
    OwnerNotifier,
    SimulatedTelegramTransport,
    notifications_from_briefing,
)

log = logging.getLogger(__name__)

TOOL_NAME: Final = "ops_morning_briefing"
TASK_TAG_PREPARE: Final = "ops:morning-prepare"
TASK_TAG_OVERVIEW: Final = "ops:morning-overview"
TASK_TAGS: Final[tuple[str, ...]] = (TASK_TAG_PREPARE, TASK_TAG_OVERVIEW)
TASK_TITLES: Final[dict[str, str]] = {
    TASK_TAG_PREPARE: "Morning briefing (prepare)",
    TASK_TAG_OVERVIEW: "Daily overview (Telegram)",
}
DEFAULT_PREPARE_TIME: Final = "06:30"
DEFAULT_OVERVIEW_TIME: Final = "07:00"
MODES: Final[tuple[str, ...]] = ("prepare", "overview")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ops_morning_schedule (
    id                INTEGER PRIMARY KEY CHECK (id = 1),
    enabled           INTEGER NOT NULL DEFAULT 0,
    prepare_time      TEXT NOT NULL,
    overview_time     TEXT NOT NULL,
    timezone          TEXT NOT NULL,
    language          TEXT,
    include_calendar  INTEGER NOT NULL DEFAULT 1,
    updated_ms        INTEGER NOT NULL
)
"""

_SNAPSHOT_SCHEMA = """
CREATE TABLE IF NOT EXISTS ops_briefing_snapshot (
    day          TEXT PRIMARY KEY,
    language     TEXT NOT NULL,
    text         TEXT NOT NULL,
    spoken       TEXT NOT NULL,
    composed_at  TEXT NOT NULL
)
"""
#: Prepared briefings kept (one per day).
SNAPSHOT_KEEP: Final = 14


@dataclass(frozen=True, slots=True)
class MorningSettings:
    enabled: bool = False
    prepare_time: str = DEFAULT_PREPARE_TIME
    overview_time: str = DEFAULT_OVERVIEW_TIME
    timezone: str = ""
    language: str | None = None
    include_calendar: bool = True
    updated_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "prepare_time": self.prepare_time,
            "overview_time": self.overview_time,
            "timezone": self.timezone,
            "language": self.language,
            "include_calendar": self.include_calendar,
            "prepare_output": "none",  # stands ready; never spoken or sent unasked
            "overview_delivery": "telegram, simulated unless the live switch is on",
            "updated_ms": self.updated_ms,
        }


def validate_settings(settings: MorningSettings) -> MorningSettings:
    """Raise ``ValueError`` for a time or timezone the scheduler would refuse."""
    from jarvis.tasks.schema import TriggerCalendar

    if settings.enabled or settings.timezone:
        for moment in (settings.prepare_time, settings.overview_time):
            TriggerCalendar(timezone=settings.timezone, local_time=moment)
    if settings.language is not None:
        normalize_language(settings.language)
    return settings


async def _connect(db_path: Path, schema: str) -> aiosqlite.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(db_path)
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA busy_timeout = 5000")
    await conn.execute(schema)
    return conn


class MorningStore:
    """The morning schedule switch, in the Ops core's own SQLite file."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)

    async def settings(self) -> MorningSettings:
        conn = await _connect(self._db_path, _SCHEMA)
        try:
            cur = await conn.execute("SELECT * FROM ops_morning_schedule WHERE id = 1")
            row = await cur.fetchone()
        finally:
            await conn.close()
        if row is None:
            return MorningSettings()
        return MorningSettings(
            enabled=bool(row["enabled"]),
            prepare_time=str(row["prepare_time"]),
            overview_time=str(row["overview_time"]),
            timezone=str(row["timezone"]),
            language=row["language"],
            include_calendar=bool(row["include_calendar"]),
            updated_ms=int(row["updated_ms"]),
        )

    async def save(self, settings: MorningSettings) -> MorningSettings:
        settings = validate_settings(settings)
        stored = replace(settings, updated_ms=int(time.time() * 1000))
        conn = await _connect(self._db_path, _SCHEMA)
        try:
            await conn.execute(
                "INSERT INTO ops_morning_schedule (id, enabled, prepare_time, overview_time,"
                " timezone, language, include_calendar, updated_ms)"
                " VALUES (1, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT (id) DO UPDATE SET enabled = excluded.enabled,"
                " prepare_time = excluded.prepare_time, overview_time = excluded.overview_time,"
                " timezone = excluded.timezone, language = excluded.language,"
                " include_calendar = excluded.include_calendar, updated_ms = excluded.updated_ms",
                (
                    int(stored.enabled),
                    stored.prepare_time,
                    stored.overview_time,
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


@dataclass(frozen=True, slots=True)
class BriefingSnapshot:
    day: str
    language: str
    text: str
    spoken: str
    composed_at: str


class BriefingSnapshotStore:
    """The briefing prepared at the morning's prepare moment, one per day."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)

    async def save(self, snapshot: BriefingSnapshot) -> None:
        conn = await _connect(self._db_path, _SNAPSHOT_SCHEMA)
        try:
            await conn.execute(
                "INSERT INTO ops_briefing_snapshot (day, language, text, spoken, composed_at)"
                " VALUES (?, ?, ?, ?, ?) ON CONFLICT (day) DO UPDATE SET"
                " language = excluded.language, text = excluded.text,"
                " spoken = excluded.spoken, composed_at = excluded.composed_at",
                (
                    snapshot.day,
                    snapshot.language,
                    snapshot.text,
                    snapshot.spoken,
                    snapshot.composed_at,
                ),
            )
            await conn.execute(
                "DELETE FROM ops_briefing_snapshot WHERE day NOT IN ("
                " SELECT day FROM ops_briefing_snapshot ORDER BY day DESC LIMIT ?)",
                (SNAPSHOT_KEEP,),
            )
            await conn.commit()
        finally:
            await conn.close()

    async def get(self, day: date) -> BriefingSnapshot | None:
        conn = await _connect(self._db_path, _SNAPSHOT_SCHEMA)
        try:
            cur = await conn.execute(
                "SELECT * FROM ops_briefing_snapshot WHERE day = ?", (day.isoformat(),)
            )
            row = await cur.fetchone()
        finally:
            await conn.close()
        if row is None:
            return None
        return BriefingSnapshot(
            str(row["day"]),
            str(row["language"]),
            str(row["text"]),
            str(row["spoken"]),
            str(row["composed_at"]),
        )


# ---------------------------------------------------------------- schedule


def task_spec(settings: MorningSettings, tag: str, task_id: str | None = None) -> Any:
    """The recurring task for one moment (*tag*: prepare or overview).

    *task_id* keeps an existing task's identity when its schedule is replaced.
    """
    from uuid import UUID

    from jarvis.tasks.schema import TaskSpec, ToolCallAction, TriggerCalendar

    mode = "prepare" if tag == TASK_TAG_PREPARE else "overview"
    moment = settings.prepare_time if mode == "prepare" else settings.overview_time
    identity: dict[str, Any] = {"id": UUID(task_id)} if task_id else {}
    return TaskSpec(
        **identity,
        title=TASK_TITLES[tag],
        trigger=TriggerCalendar(timezone=settings.timezone, local_time=moment),
        action=ToolCallAction(
            tool_name=TOOL_NAME,
            args={
                "mode": mode,
                "slot": moment,
                "timezone": settings.timezone,
                "language": settings.language,
                "include_calendar": settings.include_calendar,
            },
        ),
        tags=(tag,),
    )


async def _tagged_tasks(store: Any, tag: str) -> list[dict[str, Any]]:
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
        if tag in tags:
            tagged.append(row)
    tagged.sort(key=lambda r: int(r.get("created_at_ns") or 0))
    return tagged


async def apply_schedule(
    settings: MorningSettings, *, scheduler: Any, store: Any
) -> dict[str, str | None]:
    """Make the scheduler match *settings*: one task per moment, or paused.

    On: update and resume each tagged task, or create it. Off: pause them.
    Extra tagged tasks (should never exist) are cancelled, so no moment can
    ever be scheduled twice. Returns the task id per tag.
    """
    ids: dict[str, str | None] = {}
    for tag in TASK_TAGS:
        tasks = await _tagged_tasks(store, tag)
        keep = tasks[0] if tasks else None
        for extra in tasks[1:]:
            await scheduler.cancel_task(str(extra["id"]), reason="duplicate_morning_task")
        if not settings.enabled:
            if keep is not None and keep["state"] != "paused":
                await scheduler.pause(str(keep["id"]))
            ids[tag] = str(keep["id"]) if keep is not None else None
            continue
        if keep is None:
            ids[tag] = str(await scheduler.schedule(task_spec(settings, tag)))
            continue
        task_id = str(keep["id"])
        await scheduler.update_task(task_id, task_spec(settings, tag, task_id))
        current = await store.get(task_id)
        if current is not None and current["state"] == "paused":
            await scheduler.resume(task_id)
        ids[tag] = task_id
    return ids


def overview_key(day: date, slot: str) -> str:
    """One overview per day AND slot — independent of a voice briefing."""
    return f"overview:{day.isoformat()}@{slot}"


# ---------------------------------------------------------------- tool


class MorningBriefingTool:
    """prepare: compose and store the briefing (no output). overview: read the
    calendar again and send the day's overview through the notifier."""

    name = TOOL_NAME
    description = (
        "Internal: prepare the person's morning briefing, or send the daily overview "
        "through the notifier. Run by the task scheduler only."
    )
    risk_tier = "monitor"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "mode": {"type": "string", "enum": list(MODES)},
            "slot": {"type": "string"},
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
        snapshot_store: Callable[[], BriefingSnapshotStore | None] = lambda: None,
        transport: Callable[[NotifySettings], NotificationTransport] = (
            lambda _settings: SimulatedTelegramTransport()
        ),
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._composer = composer
        self._notify_store = notify_store
        self._snapshot_store = snapshot_store
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
        mode = str(args.get("mode") or "overview")
        if mode not in MODES:
            return ToolResult(False, None, "unknown_mode")
        composer = self._composer()
        if composer is None:
            return ToolResult(False, None, "ops_core_unavailable")
        now = self._clock().astimezone(zone)
        language = normalize_language(str(args.get("language") or "en"))
        try:
            # The calendar is read fresh on every run: changes since the
            # prepare moment are in the overview.
            briefing = await composer.compose(
                now=now,
                language=language,
                include_calendar=bool(args.get("include_calendar", True)),
            )
            if mode == "prepare":
                return await self._prepare(briefing, now)
            return await self._overview(briefing, str(args.get("slot") or now.strftime("%H:%M")))
        except Exception as exc:  # noqa: BLE001 — the run fails visibly, the schedule stays
            log.warning("ops morning %s failed (%s)", mode, type(exc).__name__, exc_info=True)
            return ToolResult(False, None, f"morning_{mode}_failed:{type(exc).__name__}")

    async def _prepare(self, briefing: Briefing, now: datetime) -> ToolResult:
        from jarvis.ops.briefing_service import spoken_briefing, spoken_table

        store = self._snapshot_store()
        if store is None:
            return ToolResult(False, None, "ops_core_unavailable")
        await store.save(
            BriefingSnapshot(
                briefing.day.isoformat(),
                briefing.language,
                briefing.text,
                spoken_briefing(briefing, spoken_table(briefing.language)),
                now.isoformat(timespec="minutes"),
            )
        )
        log.info("ops morning briefing prepared for %s", briefing.day.isoformat())
        # Nothing is said or sent: the briefing stands ready to be asked for.
        return ToolResult(True, {"mode": "prepare", "day": briefing.day.isoformat(), "sent": 0})

    async def _overview(self, briefing: Briefing, slot: str) -> ToolResult:
        store = self._notify_store()
        if store is None:
            return ToolResult(False, None, "ops_core_unavailable")
        notes = [
            replace(n, dedup_key=overview_key(briefing.day, slot))
            if n.kind == "daily_briefing"
            else n
            for n in notifications_from_briefing(briefing)
        ]
        # Simulated unless the owner's live switch is on (jarvis/ops/delivery.py).
        transport = self._transport(await store.settings())
        report = await OwnerNotifier(store, transport).deliver(notes)
        counts = report.to_dict()["counts"]
        delivered = report.count("simulated") + report.count("delivered")
        log.info(
            "ops daily overview %s@%s: %d delivered (%s)",
            briefing.day.isoformat(),
            slot,
            delivered,
            counts,
        )
        return ToolResult(
            True,
            {
                "mode": "overview",
                "day": briefing.day.isoformat(),
                "slot": slot,
                "delivered": delivered,
                "counts": counts,
                "simulated": bool(getattr(transport, "simulated", True)),
            },
        )


def ops_task_tools(
    *,
    composer: Callable[[], BriefingComposer | None],
    notify_store: Callable[[], NotifyStore | None],
    snapshot_store: Callable[[], BriefingSnapshotStore | None] | None = None,
    transport: Callable[[NotifySettings], NotificationTransport] | None = None,
) -> dict[str, Any]:
    """The task runner's private tool registry: the Ops core's scheduled tools."""
    kwargs: dict[str, Any] = {"composer": composer, "notify_store": notify_store}
    if snapshot_store is not None:
        kwargs["snapshot_store"] = snapshot_store
    if transport is not None:
        kwargs["transport"] = transport
    return {TOOL_NAME: MorningBriefingTool(**kwargs)}


__all__ = [
    "DEFAULT_OVERVIEW_TIME",
    "DEFAULT_PREPARE_TIME",
    "MODES",
    "TASK_TAGS",
    "TASK_TAG_OVERVIEW",
    "TASK_TAG_PREPARE",
    "TOOL_NAME",
    "BriefingSnapshot",
    "BriefingSnapshotStore",
    "MorningBriefingTool",
    "MorningSettings",
    "MorningStore",
    "apply_schedule",
    "ops_task_tools",
    "overview_key",
    "task_spec",
    "validate_settings",
]
