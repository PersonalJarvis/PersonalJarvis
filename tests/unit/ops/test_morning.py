"""Daily morning schedule through the existing task scheduler.

06:30 Europe/Madrid prepares the briefing (nothing spoken or sent), 07:00
reads the calendar again and sends the day's overview — once per day and
slot, whether or not the briefing was heard by voice. Off by default,
DST-correct, failures never crash the scheduler, delivery simulated. The full
path runs through the real TaskScheduler, TaskRunner and ToolExecutor with
real stores (temp files).
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from jarvis.control.cancel import CancelToken
from jarvis.core.bus import EventBus
from jarvis.core.config import SafetyConfig
from jarvis.ops.briefing import BriefingComposer
from jarvis.ops.calendar_day import CalendarDay, classify_day
from jarvis.ops.ledger import WorkItem, WorkSnapshot
from jarvis.ops.morning import (
    DEFAULT_OVERVIEW_TIME,
    DEFAULT_PREPARE_TIME,
    TASK_TAG_OVERVIEW,
    TASK_TAG_PREPARE,
    TASK_TAGS,
    TOOL_NAME,
    BriefingSnapshotStore,
    MorningBriefingTool,
    MorningSettings,
    MorningStore,
    apply_schedule,
    ops_task_tools,
    overview_key,
    task_spec,
)
from jarvis.ops.notify import NotifyStore, SimulatedTelegramTransport
from jarvis.ops.priority import PriorityMark
from jarvis.safety.approval import ApprovalWorkflow
from jarvis.safety.risk_tier import RiskTierEvaluator
from jarvis.safety.tool_executor import ToolExecutor
from jarvis.tasks.runner import TaskRunner
from jarvis.tasks.scheduler import TaskScheduler, next_every_due_ns
from jarvis.tasks.store import TaskStore

MADRID = "Europe/Madrid"
ON = MorningSettings(True, "06:30", "07:00", MADRID, "en")
CANCELLED = {
    "id": "call",
    "summary": "Client call",
    "start": "2026-10-07T12:00:00+02:00",
    "status": "cancelled",
    "updated": "2026-10-07T05:00:00Z",
}
# 06:30 and 07:00 Europe/Madrid (CEST, UTC+2) on 2026-10-07.
AT_PREP = "2026-10-07T04:30:00+00:00"
AT_OVER = "2026-10-07T05:00:00+00:00"


# --- Settings ----------------------------------------------------------------------


def test_the_defaults_are_0630_and_0700_and_off() -> None:
    settings = MorningSettings()
    assert (settings.enabled, DEFAULT_PREPARE_TIME, DEFAULT_OVERVIEW_TIME) == (
        False,
        "06:30",
        "07:00",
    )
    assert settings.to_dict()["prepare_output"] == "none"


async def test_off_by_default_and_validated(tmp_path: Path) -> None:
    store = MorningStore(tmp_path / "ops.sqlite")
    assert (await store.settings()).enabled is False
    with pytest.raises(ValueError):
        await store.save(MorningSettings(enabled=True, timezone="Mars/Olympus"))
    with pytest.raises(ValueError):
        await store.save(MorningSettings(True, "06:30", "25:00", MADRID))
    saved = await store.save(ON)
    assert (await store.settings()) == saved


def test_two_wall_clock_tasks_prepare_and_overview() -> None:
    prepare = task_spec(ON, TASK_TAG_PREPARE)
    overview = task_spec(ON, TASK_TAG_OVERVIEW)
    assert (prepare.trigger.local_time, overview.trigger.local_time) == ("06:30", "07:00")
    assert prepare.trigger.timezone == overview.trigger.timezone == MADRID
    assert prepare.action.args["mode"] == "prepare" and overview.action.args["mode"] == "overview"
    assert overview.action.args["slot"] == "07:00"
    assert (prepare.tags, overview.tags) == ((TASK_TAG_PREPARE,), (TASK_TAG_OVERVIEW,))
    assert prepare.action.tool_name == overview.action.tool_name == TOOL_NAME


def _ns(text: str) -> int:
    return int(datetime.fromisoformat(text).timestamp() * 1_000_000_000)


def test_the_times_stay_local_across_the_dst_change() -> None:
    zone = ZoneInfo(MADRID)
    for tag, moment in ((TASK_TAG_PREPARE, "06:30"), (TASK_TAG_OVERVIEW, "07:00")):
        spec = task_spec(ON, tag)
        before = next_every_due_ns(spec, _ns("2026-10-24T10:00:00+02:00"))
        after = next_every_due_ns(spec, _ns("2026-10-25T10:00:00+01:00"))
        local = [
            datetime.fromtimestamp(n / 1e9, zone).strftime("%Y-%m-%d %H:%M %Z")
            for n in (before, after)
        ]
        assert local == [f"2026-10-25 {moment} CET", f"2026-10-26 {moment} CET"]
    summer = next_every_due_ns(task_spec(ON, TASK_TAG_OVERVIEW), _ns("2026-10-23T10:00:00+02:00"))
    assert datetime.fromtimestamp(summer / 1e9, UTC).strftime("%H:%M") == "05:00"  # 07:00 CEST


# --- Schedule ------------------------------------------------------------------------


@pytest.fixture
async def tasks(tmp_path: Path):
    store = TaskStore(tmp_path / "tasks.db")
    await store.init()
    scheduler = TaskScheduler(store=store, bus=EventBus())
    try:
        yield store, scheduler
    finally:
        await scheduler.shutdown()
        await store.close()


async def _tagged(store: TaskStore, tag: str) -> list[dict[str, Any]]:
    rows = await store.list(limit=100)
    return [r for r in rows if tag in json.loads(r["spec_json"]).get("tags", [])]


async def test_on_off_on_keeps_exactly_one_task_per_moment(tasks: Any) -> None:
    store, scheduler = tasks
    off = await apply_schedule(MorningSettings(False), scheduler=scheduler, store=store)
    assert off == dict.fromkeys(TASK_TAGS) and await _tagged(store, TASK_TAG_PREPARE) == []

    first = await apply_schedule(ON, scheduler=scheduler, store=store)
    moved = MorningSettings(True, "08:15", "09:05", MADRID, "en")
    again = await apply_schedule(moved, scheduler=scheduler, store=store)
    assert first == again
    for tag, moment in ((TASK_TAG_PREPARE, "08:15"), (TASK_TAG_OVERVIEW, "09:05")):
        [row] = await _tagged(store, tag)
        assert row["state"] == "scheduled"
        spec = await store.get_spec(row["id"])
        assert spec is not None and spec.trigger.local_time == moment

    await apply_schedule(MorningSettings(False, timezone=MADRID), scheduler=scheduler, store=store)
    for tag in TASK_TAGS:
        assert [r["state"] for r in await _tagged(store, tag)] == ["paused"]
    await apply_schedule(ON, scheduler=scheduler, store=store)
    for tag in TASK_TAGS:
        assert [r["state"] for r in await _tagged(store, tag)] == ["scheduled"]


async def test_a_second_task_for_the_same_moment_is_cancelled(tasks: Any) -> None:
    store, scheduler = tasks
    keep = await scheduler.schedule(task_spec(ON, TASK_TAG_OVERVIEW))
    await scheduler.schedule(task_spec(ON, TASK_TAG_OVERVIEW))  # must not survive
    ids = await apply_schedule(ON, scheduler=scheduler, store=store)
    assert ids[TASK_TAG_OVERVIEW] == keep
    states = sorted(r["state"] for r in await _tagged(store, TASK_TAG_OVERVIEW))
    assert states == ["cancelled", "scheduled"]


# --- The tool ------------------------------------------------------------------------


class FakeComposerSource:
    """A real BriefingComposer over fake facts; the calendar can change."""

    def __init__(self, *, fail: Exception | None = None) -> None:
        self.nows: list[datetime] = []
        self.fail = fail
        self.events: list[dict[str, Any]] = [CANCELLED]

    def __call__(self) -> BriefingComposer:
        async def _snapshot() -> WorkSnapshot:
            if self.fail is not None:
                raise self.fail
            return WorkSnapshot((WorkItem("task", "t1", "Pay invoice", "queued", "pending"),))

        async def _marks() -> list[PriorityMark]:
            return [PriorityMark("task", "t1", "urgent")]

        source = self

        class _Calendar:
            async def read_day(self, day: Any, now: datetime) -> CalendarDay:
                source.nows.append(now)
                upcoming, cancelled = classify_day(day, source.events, tz=now.tzinfo, now=now)
                return CalendarDay("ok", upcoming, cancelled)

        return BriefingComposer(snapshot=_snapshot, marks=_marks, calendar=_Calendar())


@pytest.fixture
def notify(tmp_path: Path) -> NotifyStore:
    return NotifyStore(tmp_path / "ops.sqlite")


@pytest.fixture
def snapshots(tmp_path: Path) -> BriefingSnapshotStore:
    return BriefingSnapshotStore(tmp_path / "ops.sqlite")


def _tool(
    notify: NotifyStore,
    composer: Any,
    clock: str = AT_OVER,
    transport: SimulatedTelegramTransport | None = None,
    snapshots: BriefingSnapshotStore | None = None,
) -> MorningBriefingTool:
    sent = transport or SimulatedTelegramTransport()
    return MorningBriefingTool(
        composer=composer,
        notify_store=lambda: notify,
        snapshot_store=lambda: snapshots,
        transport=lambda _settings: sent,
        clock=lambda: datetime.fromisoformat(clock),
    )


def _overview(**kw: Any) -> dict[str, Any]:
    return {"mode": "overview", "slot": "07:00", "timezone": MADRID, "language": "en", **kw}


async def test_0630_prepares_and_neither_speaks_nor_sends(
    notify: NotifyStore, snapshots: BriefingSnapshotStore
) -> None:
    await notify.save_settings(enabled=True, kinds=["daily_briefing", "appointment_cancelled"])
    transport = SimulatedTelegramTransport()
    result = await _tool(notify, FakeComposerSource(), AT_PREP, transport, snapshots).execute(
        {"mode": "prepare", "timezone": MADRID, "language": "en"}
    )
    assert result.success and result.output == {"mode": "prepare", "day": "2026-10-07", "sent": 0}
    assert transport.sent == [] and await notify.outbox() == []
    snapshot = await snapshots.get(datetime(2026, 10, 7).date())
    assert snapshot is not None and snapshot.composed_at.startswith("2026-10-07T06:30")
    assert snapshot.text.startswith("Briefing for 2026-10-07")
    assert "Cancelled: Client call 12:00." in snapshot.spoken


async def test_0700_reads_the_calendar_again_and_sends_once_per_slot(
    notify: NotifyStore, snapshots: BriefingSnapshotStore
) -> None:
    await notify.save_settings(enabled=True, kinds=["daily_briefing", "appointment_cancelled"])
    source = FakeComposerSource()
    transport = SimulatedTelegramTransport()
    await _tool(notify, source, AT_PREP, transport, snapshots).execute(
        {"mode": "prepare", "timezone": MADRID}
    )
    # Between 06:30 and 07:00 a new appointment appears.
    source.events = [
        *source.events,
        {
            "id": "dentist",
            "summary": "Dentist",
            "start": "2026-10-07T11:00:00+02:00",
            "created": "2026-10-07T04:45:00Z",
        },
    ]
    first = await _tool(notify, source, AT_OVER, transport, snapshots).execute(_overview())
    again = await _tool(notify, source, AT_OVER, transport, snapshots).execute(_overview())
    assert first.output["delivered"] == 2 and again.output["delivered"] == 0
    overview = transport.sent[-1]
    assert "New appointments (1):\n- 11:00 Dentist" in overview  # the 07:00 refresh
    assert "Cancelled appointments (1):" in overview
    assert await notify.get(overview_key(datetime(2026, 10, 7).date(), "07:00")) is not None
    assert [n.strftime("%H:%M") for n in source.nows] == ["06:30", "07:00", "07:00"]


async def test_a_voice_briefing_does_not_stop_the_0700_overview(notify: NotifyStore) -> None:
    await notify.save_settings(enabled=True, kinds=["daily_briefing"])
    await notify.note_delivered(  # heard by voice at 08:40
        dedup_key="briefing:2026-10-07", kind="daily_briefing", transport="voice"
    )
    transport = SimulatedTelegramTransport()
    result = await _tool(notify, FakeComposerSource(), AT_OVER, transport).execute(_overview())
    assert result.output["delivered"] == 1
    assert transport.sent[0].startswith("Briefing for 2026-10-07")


@pytest.mark.parametrize(
    "zone,day",
    [("Asia/Tokyo", "2026-10-08"), ("Pacific/Honolulu", "2026-10-07"), (MADRID, "2026-10-08")],
)
async def test_the_day_is_the_persons_local_day(notify: NotifyStore, zone: str, day: str) -> None:
    source = FakeComposerSource()
    result = await _tool(notify, source, "2026-10-07T22:30:00+00:00").execute(
        _overview(timezone=zone)
    )
    assert result.output["day"] == day
    assert str(source.nows[0].tzinfo) == zone


async def test_failures_are_results_not_crashes(notify: NotifyStore) -> None:
    bad_zone = await _tool(notify, FakeComposerSource()).execute(_overview(timezone="Nowhere/Land"))
    assert (bad_zone.success, bad_zone.error) == (False, "invalid_timezone")
    # A dead source does not stop the 07:00 overview: it is named, the rest goes out.
    await notify.save_settings(enabled=True, kinds=["daily_briefing"])
    sent = SimulatedTelegramTransport()
    broken = await _tool(
        notify, FakeComposerSource(fail=RuntimeError("db locked")), transport=sent
    ).execute(_overview())
    assert broken.success and broken.output["delivered"] == 1
    assert "Not reachable right now: work items." in sent.sent[0]
    assert "Cancelled appointments (1):" in sent.sent[0]  # the calendar still answered
    unknown = await _tool(notify, FakeComposerSource()).execute(_overview(mode="speak"))
    assert (unknown.success, unknown.error) == (False, "unknown_mode")
    missing = await MorningBriefingTool(composer=lambda: None, notify_store=lambda: notify).execute(
        _overview()
    )
    assert (missing.success, missing.error) == (False, "ops_core_unavailable")
    no_snapshots = await _tool(notify, FakeComposerSource()).execute(
        {"mode": "prepare", "timezone": MADRID}
    )
    assert (no_snapshots.success, no_snapshots.error) == (False, "ops_core_unavailable")


def test_the_tool_stays_out_of_the_chat_and_router_tools() -> None:
    from jarvis.brain.factory import ROUTER_TOOLS

    assert TOOL_NAME not in ROUTER_TOOLS
    assert MorningBriefingTool.risk_tier == "monitor"


# --- End to end: scheduler -> runner -> ToolExecutor -> tool -> notifier ----------


async def test_both_scheduled_runs_through_the_real_executor(
    tasks: Any, notify: NotifyStore, snapshots: BriefingSnapshotStore
) -> None:
    store, _scheduler = tasks
    await notify.save_settings(enabled=True, kinds=["daily_briefing"])
    bus = EventBus()
    executor = ToolExecutor(bus, RiskTierEvaluator(SafetyConfig()), ApprovalWorkflow(bus))
    transport = SimulatedTelegramTransport()
    tools = {TOOL_NAME: _tool(notify, FakeComposerSource(), AT_OVER, transport, snapshots)}
    runner = TaskRunner(store, bus, tool_executor=executor, tool_registry=tools)
    scheduler = TaskScheduler(store=store, bus=bus, runner=runner)
    ids = await apply_schedule(ON, scheduler=scheduler, store=store)

    await asyncio.wait_for(runner.run(ids[TASK_TAG_PREPARE], CancelToken()), timeout=5)
    assert transport.sent == []  # 06:30: nothing goes out
    for _ in range(2):  # 07:00 fires, then a restart fires it again
        await asyncio.wait_for(runner.run(ids[TASK_TAG_OVERVIEW], CancelToken()), timeout=5)

    assert [m.split("\n")[0] for m in transport.sent] == ["Briefing for 2026-10-07"]
    for tag in TASK_TAGS:
        row = await store.get(ids[tag])
        assert row is not None and row["state"] == "scheduled"  # recurring, stays on


async def test_a_run_before_the_brain_is_ready_fails_and_keeps_the_schedule(
    tasks: Any, notify: NotifyStore
) -> None:
    store, _scheduler = tasks

    class _NotReady:
        async def execute(self, *a: Any, **k: Any) -> Any:
            raise RuntimeError("The tool executor is not ready yet")

    bus = EventBus()
    runner = TaskRunner(
        store,
        bus,
        tool_executor=_NotReady(),
        tool_registry=ops_task_tools(composer=FakeComposerSource(), notify_store=lambda: notify),
    )
    scheduler = TaskScheduler(store=store, bus=bus, runner=runner)
    ids = await apply_schedule(ON, scheduler=scheduler, store=store)
    await asyncio.wait_for(runner.run(ids[TASK_TAG_OVERVIEW], CancelToken()), timeout=5)
    row = await store.get(ids[TASK_TAG_OVERVIEW])
    assert row is not None and row["state"] == "scheduled"
    assert "not ready" in (row["last_error"] or "")
    assert await notify.outbox() == []
