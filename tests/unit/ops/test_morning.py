"""Automatic morning briefing through the existing task scheduler.

Off by default; one recurring task when on; DST-correct wall-clock time in the
person's IANA timezone; one briefing per local day; failures never crash the
scheduler; delivery simulated only. The full path runs through the real
TaskScheduler, TaskRunner and ToolExecutor with real stores (temp files).
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
from jarvis.ops.calendar_day import CalendarDay
from jarvis.ops.ledger import WorkItem, WorkSnapshot
from jarvis.ops.morning import (
    TASK_TAG,
    TOOL_NAME,
    MorningBriefingTool,
    MorningSettings,
    MorningStore,
    apply_schedule,
    ops_task_tools,
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

BERLIN = "Europe/Berlin"
CANCELLED = {
    "id": "call",
    "summary": "Client call",
    "start": "2026-10-07T12:00:00+02:00",
    "status": "cancelled",
    "updated": "2026-10-07T05:00:00Z",
}


# --- Settings -------------------------------------------------------------------


async def test_off_by_default_and_validated(tmp_path: Path) -> None:
    store = MorningStore(tmp_path / "ops.sqlite")
    assert (await store.settings()).enabled is False
    with pytest.raises(ValueError):
        await store.save(MorningSettings(enabled=True, timezone="Mars/Olympus"))
    with pytest.raises(ValueError):
        await store.save(MorningSettings(enabled=True, local_time="25:00", timezone=BERLIN))
    saved = await store.save(MorningSettings(enabled=True, local_time="06:45", timezone=BERLIN))
    assert (await store.settings()) == saved


def test_the_task_is_a_wall_clock_calendar_task_with_the_tool() -> None:
    spec = task_spec(MorningSettings(True, "06:45", BERLIN, "de"))
    assert spec.trigger.type == "calendar"
    assert (spec.trigger.timezone, spec.trigger.local_time) == (BERLIN, "06:45")
    assert spec.action.kind == "tool_call" and spec.action.tool_name == TOOL_NAME
    assert spec.action.args == {"timezone": BERLIN, "language": "de", "include_calendar": True}
    assert spec.tags == (TASK_TAG,)


def _ns(text: str) -> int:
    return int(datetime.fromisoformat(text).timestamp() * 1_000_000_000)


def test_the_time_stays_07_00_local_across_the_dst_change() -> None:
    spec = task_spec(MorningSettings(True, "07:00", BERLIN))
    before = next_every_due_ns(spec, _ns("2026-10-24T08:00:00+02:00"))
    after = next_every_due_ns(spec, _ns("2026-10-25T08:00:00+01:00"))
    zone = ZoneInfo(BERLIN)
    as_local = [datetime.fromtimestamp(n / 1e9, zone) for n in (before, after)]
    assert [d.strftime("%Y-%m-%d %H:%M %Z") for d in as_local] == [
        "2026-10-25 07:00 CET",
        "2026-10-26 07:00 CET",
    ]
    summer = next_every_due_ns(spec, _ns("2026-10-23T08:00:00+02:00"))
    assert datetime.fromtimestamp(summer / 1e9, UTC).hour == 5  # 07:00 CEST


# --- Schedule ---------------------------------------------------------------------


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


async def _tagged(store: TaskStore) -> list[dict[str, Any]]:
    rows = await store.list(limit=100)
    return [r for r in rows if TASK_TAG in json.loads(r["spec_json"]).get("tags", [])]


async def test_on_off_on_keeps_exactly_one_task(tasks: Any) -> None:
    store, scheduler = tasks
    off = await apply_schedule(MorningSettings(False), scheduler=scheduler, store=store)
    assert off is None and await _tagged(store) == []

    first = await apply_schedule(
        MorningSettings(True, "07:00", BERLIN), scheduler=scheduler, store=store
    )
    again = await apply_schedule(
        MorningSettings(True, "06:30", BERLIN), scheduler=scheduler, store=store
    )
    assert first == again
    [row] = await _tagged(store)
    assert row["state"] == "scheduled"
    spec = await store.get_spec(first)
    assert spec is not None and spec.trigger.local_time == "06:30"

    await apply_schedule(MorningSettings(False, "06:30", BERLIN), scheduler=scheduler, store=store)
    assert [r["state"] for r in await _tagged(store)] == ["paused"]
    await apply_schedule(MorningSettings(True, "06:30", BERLIN), scheduler=scheduler, store=store)
    assert [r["state"] for r in await _tagged(store)] == ["scheduled"]


async def test_a_second_tagged_task_is_cancelled(tasks: Any) -> None:
    store, scheduler = tasks
    settings = MorningSettings(True, "07:00", BERLIN)
    keep = await scheduler.schedule(task_spec(settings))
    await scheduler.schedule(task_spec(settings))  # a duplicate that must not survive
    assert await apply_schedule(settings, scheduler=scheduler, store=store) == keep
    states = sorted(r["state"] for r in await _tagged(store))
    assert states == ["cancelled", "scheduled"]


# --- The tool ---------------------------------------------------------------------


class FakeComposerSource:
    """A real BriefingComposer over fake facts; records the ``now`` it got."""

    def __init__(self, *, fail: Exception | None = None) -> None:
        self.nows: list[datetime] = []
        self.fail = fail

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
                from jarvis.ops.calendar_day import classify_day

                upcoming, cancelled = classify_day(day, [CANCELLED], tz=now.tzinfo, now=now)
                return CalendarDay("ok", upcoming, cancelled)

        return BriefingComposer(snapshot=_snapshot, marks=_marks, calendar=_Calendar())


@pytest.fixture
def notify(tmp_path: Path) -> NotifyStore:
    return NotifyStore(tmp_path / "ops.sqlite")


def _tool(
    notify: NotifyStore,
    composer: Any,
    clock: str = "2026-10-07T04:30:00+00:00",
    transport: SimulatedTelegramTransport | None = None,
) -> MorningBriefingTool:
    sent = transport or SimulatedTelegramTransport()
    return MorningBriefingTool(
        composer=composer,
        notify_store=lambda: notify,
        transport=lambda: sent,
        clock=lambda: datetime.fromisoformat(clock),
    )


async def test_with_notifications_off_nothing_is_sent(notify: NotifyStore) -> None:
    transport = SimulatedTelegramTransport()
    result = await _tool(notify, FakeComposerSource(), transport=transport).execute(
        {"timezone": BERLIN}
    )
    assert result.success and result.output["delivered"] == 0
    assert set(result.output["counts"]) == {"disabled"}
    assert transport.sent == []


async def test_once_per_local_day_and_short_notice_first(notify: NotifyStore) -> None:
    await notify.save_settings(
        enabled=True,
        kinds=["daily_briefing", "appointment_cancelled", "important_task"],
    )
    transport = SimulatedTelegramTransport()
    tool = _tool(notify, FakeComposerSource(), transport=transport)
    first = await tool.execute({"timezone": BERLIN, "language": "en"})
    second = await tool.execute({"timezone": BERLIN, "language": "en"})
    assert first.output["day"] == "2026-10-07" and first.output["delivered"] == 3
    assert transport.sent[0].startswith("Appointment cancelled: (!)")
    assert transport.sent[-1].startswith("Briefing for 2026-10-07")
    assert "Cancelled appointments (1):" in transport.sent[-1]
    assert second.output["delivered"] == 0 and set(second.output["counts"]) == {"duplicate"}
    assert len(transport.sent) == 3
    assert first.output["simulated"] is True


@pytest.mark.parametrize(
    "zone,day",
    [("Asia/Tokyo", "2026-10-08"), ("Pacific/Honolulu", "2026-10-07"), (BERLIN, "2026-10-08")],
)
async def test_the_day_is_the_persons_local_day(notify: NotifyStore, zone: str, day: str) -> None:
    source = FakeComposerSource()
    result = await _tool(notify, source, clock="2026-10-07T22:30:00+00:00").execute(
        {"timezone": zone}
    )
    assert result.output["day"] == day
    assert str(source.nows[0].tzinfo) == zone


async def test_failures_are_results_not_crashes(notify: NotifyStore) -> None:
    bad_zone = await _tool(notify, FakeComposerSource()).execute({"timezone": "Nowhere/Land"})
    assert (bad_zone.success, bad_zone.error) == (False, "invalid_timezone")
    broken = await _tool(notify, FakeComposerSource(fail=RuntimeError("db locked"))).execute(
        {"timezone": BERLIN}
    )
    assert (broken.success, broken.error) == (False, "morning_briefing_failed:RuntimeError")
    missing = await MorningBriefingTool(composer=lambda: None, notify_store=lambda: notify).execute(
        {"timezone": BERLIN}
    )
    assert (missing.success, missing.error) == (False, "ops_core_unavailable")


def test_the_tool_stays_out_of_the_chat_and_router_tools() -> None:
    from jarvis.brain.factory import ROUTER_TOOLS

    assert TOOL_NAME not in ROUTER_TOOLS
    assert MorningBriefingTool.risk_tier == "monitor"


# --- End to end: scheduler -> runner -> ToolExecutor -> tool -> notifier -------


async def test_scheduled_run_through_the_real_executor_sends_once(
    tasks: Any, notify: NotifyStore
) -> None:
    store, _scheduler = tasks
    await notify.save_settings(enabled=True, kinds=["daily_briefing"])
    bus = EventBus()
    executor = ToolExecutor(bus, RiskTierEvaluator(SafetyConfig()), ApprovalWorkflow(bus))
    transport = SimulatedTelegramTransport()
    tools = {TOOL_NAME: _tool(notify, FakeComposerSource(), transport=transport)}
    runner = TaskRunner(store, bus, tool_executor=executor, tool_registry=tools)
    scheduler = TaskScheduler(store=store, bus=bus, runner=runner)
    task_id = await apply_schedule(
        MorningSettings(True, "07:00", BERLIN, "en"), scheduler=scheduler, store=store
    )
    assert task_id is not None
    for _ in range(2):  # the slot fires, then a restart fires it again
        await asyncio.wait_for(runner.run(task_id, CancelToken()), timeout=5)

    row = await store.get(task_id)
    assert row is not None and row["state"] == "scheduled"  # recurring, stays on
    assert [m.split("\n")[0] for m in transport.sent] == ["Briefing for 2026-10-07"]
    results = [s["payload"] for s in row["steps"] if s["payload"].get("event") == "tool_result"]
    assert [r["success"] for r in results] == [True, True]


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
    task_id = await apply_schedule(
        MorningSettings(True, "07:00", BERLIN), scheduler=scheduler, store=store
    )
    assert task_id is not None
    await asyncio.wait_for(runner.run(task_id, CancelToken()), timeout=5)
    row = await store.get(task_id)
    assert row is not None and row["state"] == "scheduled"
    assert "not ready" in (row["last_error"] or "")
    assert await notify.outbox() == []
