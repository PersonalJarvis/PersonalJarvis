"""BUG-212 — a cron slot the app slept through is missed, not caught up.

Forensics (data-dev/workflows.sqlite, 2026-08-25 … 2026-09-02): every
"Morning Briefing" run started at the exact second the app booted — 10:15,
20:49, 15:04, 11:18 — because the scheduler fired any stored ``next_run_at_ns``
that was ``<= now``, however stale. One briefing a day, at a random hour.

The rule now: within :data:`jarvis.core.misfire.MISFIRE_GRACE_S` the slot
still fires (a slow boot at 07:31 is still the morning); older than that it
is recorded as a ``missed`` run, the schedule continues from the NEXT
occurrence, and the runner is never called.

Fakes throughout — a real store on tmp_path, a recording runner, no network.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.events import WorkflowScheduled
from jarvis.core.misfire import MISFIRE_GRACE_NS, is_missed, late_by_s
from jarvis.workflows.scheduler import WorkflowScheduler
from jarvis.workflows.schema import CronTrigger, SpeakStep, WorkflowDef
from jarvis.workflows.store import WorkflowStore

_HOUR_NS = 3_600 * 1_000_000_000


@pytest.fixture
async def store(tmp_path: Path) -> WorkflowStore:
    s = WorkflowStore(tmp_path / "wf.sqlite")
    await s.init()
    yield s
    await s.close()


class _RecordingRunner:
    def __init__(self) -> None:
        self.triggered: list[str] = []

    async def trigger(self, workflow_id: str, *, trigger_reason: str) -> str:
        self.triggered.append(workflow_id)
        return "run-1"


def _scheduled_events(bus: EventBus) -> list[WorkflowScheduled]:
    seen: list[WorkflowScheduled] = []
    bus.subscribe(WorkflowScheduled, seen.append)
    return seen


async def _daily_briefing(store: WorkflowStore, *, next_run_at_ns: int) -> str:
    wf = WorkflowDef(
        name="Morning Briefing",
        trigger=CronTrigger(expression="30 7 * * *"),
        steps=(SpeakStep(text="good morning"),),
    )
    wid = await store.upsert_workflow(wf)
    await store.set_next_run(wid, next_run_at_ns)
    return wid


# ----------------------------------------------------------------------
# The policy itself
# ----------------------------------------------------------------------

def test_a_slot_inside_the_grace_window_is_not_missed() -> None:
    now = time.time_ns()
    assert not is_missed(now - MISFIRE_GRACE_NS // 2, now)
    assert not is_missed(now, now)


def test_a_slot_older_than_the_grace_window_is_missed() -> None:
    now = time.time_ns()
    assert is_missed(now - MISFIRE_GRACE_NS - 1, now)
    assert is_missed(now - 8 * _HOUR_NS, now)  # 07:30 seen at 15:30


def test_late_by_is_in_seconds_and_never_negative() -> None:
    now = time.time_ns()
    assert late_by_s(now - 90 * 1_000_000_000, now) == pytest.approx(90.0)
    assert late_by_s(now + _HOUR_NS, now) == 0.0


# ----------------------------------------------------------------------
# The scheduler
# ----------------------------------------------------------------------

async def test_a_stale_slot_is_skipped_and_recorded_as_missed(
    store: WorkflowStore,
) -> None:
    """The 07:30 seen at 15:04 must not speak at 15:04."""
    bus = EventBus()
    events = _scheduled_events(bus)
    runner = _RecordingRunner()
    now = time.time_ns()
    stale = now - 8 * _HOUR_NS
    wid = await _daily_briefing(store, next_run_at_ns=stale)

    scheduler = WorkflowScheduler(store=store, runner=runner, bus=bus)
    await scheduler._tick()

    assert runner.triggered == [], "a missed slot must never reach the runner"

    row = await store.get_workflow(wid)
    assert row is not None
    assert row["next_run_at_ns"] > now, "the schedule continues from the next slot"
    assert row["last_run_state"] == "missed"
    assert row["last_run_at_ns"] == stale, "the miss is dated at the slot, not now"

    runs = await store.list_runs(wid)
    assert [r["state"] for r in runs] == ["missed"]
    assert runs[0]["started_at_ns"] == stale
    assert runs[0]["finished_at_ns"] >= now
    assert "not running" in (runs[0]["error"] or "")

    missed = [e for e in events if e.reason == "missed"]
    assert len(missed) == 1 and missed[0].workflow_id == wid
    assert missed[0].next_run_ns == row["next_run_at_ns"]


async def test_a_slot_inside_the_grace_window_still_fires(
    store: WorkflowStore,
) -> None:
    """A machine that took ten minutes to boot still gets its briefing."""
    bus = EventBus()
    runner = _RecordingRunner()
    now = time.time_ns()
    wid = await _daily_briefing(store, next_run_at_ns=now - 10 * 60 * 1_000_000_000)

    scheduler = WorkflowScheduler(store=store, runner=runner, bus=bus)
    await scheduler._tick()

    assert runner.triggered == [wid]
    runs = await store.list_runs(wid)
    assert all(r["state"] != "missed" for r in runs)


async def test_a_missed_slot_is_recorded_once_and_the_next_one_fires_on_time(
    store: WorkflowStore,
) -> None:
    """Second tick: nothing new to skip, nothing to fire yet."""
    bus = EventBus()
    runner = _RecordingRunner()
    now = time.time_ns()
    wid = await _daily_briefing(store, next_run_at_ns=now - 8 * _HOUR_NS)

    scheduler = WorkflowScheduler(store=store, runner=runner, bus=bus)
    await scheduler._tick()
    await scheduler._tick()

    assert runner.triggered == []
    assert len(await store.list_runs(wid)) == 1


async def test_a_store_without_missed_run_bookkeeping_still_reschedules() -> None:
    """The reschedule must never depend on the record being writable —
    a bookkeeping failure that turned back into a catch-up firing would be
    the original bug again."""

    class _BareStore:
        def __init__(self) -> None:
            self.rows: list[dict[str, Any]] = [{
                "id": "wf-1", "name": "Morning Briefing", "enabled": 1,
                "trigger_type": "cron", "cron_expression": "30 7 * * *",
                "next_run_at_ns": time.time_ns() - 8 * _HOUR_NS,
            }]

        async def list_workflows(self) -> list[dict[str, Any]]:
            return list(self.rows)

        async def set_next_run(self, wid: str, ns: int | None) -> None:
            self.rows[0]["next_run_at_ns"] = ns

    bare = _BareStore()
    runner = _RecordingRunner()
    scheduler = WorkflowScheduler(store=bare, runner=runner, bus=EventBus())  # type: ignore[arg-type]
    await scheduler._tick()

    assert runner.triggered == []
    assert bare.rows[0]["next_run_at_ns"] > time.time_ns()
