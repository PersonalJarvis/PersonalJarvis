"""TaskScheduler — run-now, pause and resume (the Automations controls).

The invariants that matter:
- ``run_now`` executes the action out of band and leaves the schedule of a
  recurring task untouched (same ``due_at_ns``, still in the heap, still
  ``scheduled`` afterwards); a one-shot task is consumed by the run.
- ``pause`` takes a recurring task out of the heap / event index and
  ``hydrate()`` leaves it alone; ``resume`` re-arms it at the NEXT occurrence,
  on the wall-clock grid of a ``start_at``-anchored task.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from jarvis.core.bus import EventBus
from jarvis.tasks.runner import TaskRunner
from jarvis.tasks.scheduler import (
    TaskNotFound,
    TaskScheduler,
    TaskStateConflict,
    next_every_due_ns,
    parse_iso_timestamp_to_ns,
)
from jarvis.tasks.schema import (
    AgentAction,
    SpeakAction,
    TaskSpec,
    TriggerAfterDelay,
    TriggerEvery,
    TriggerOnEvent,
)
from jarvis.tasks.store import TaskStore


class FakeBrain:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def run_task(self, *, prompt: str, **_: Any) -> str:
        self.calls.append(prompt)
        return f"result for {prompt}"


class RecordingRunner:
    """Records dispatches without touching the store (scheduler-only tests)."""

    def __init__(self) -> None:
        self.dispatched: list[str] = []
        self.gate = asyncio.Event()

    async def run(self, task_id: str, *_a: Any, **_k: Any) -> None:
        self.dispatched.append(task_id)
        self.gate.set()


@pytest.fixture
async def store(tmp_path: Path):
    s = TaskStore(tmp_path / "auto.db")
    await s.init()
    try:
        yield s
    finally:
        await s.close()


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


def _every(interval: float = 3600.0, start_at: str | None = None,
           prompt: str = "brief") -> TaskSpec:
    return TaskSpec(
        title="recurring",
        trigger=TriggerEvery(interval_seconds=interval, start_at=start_at),
        action=AgentAction(prompt=prompt),
    )


async def _settle(scheduler: TaskScheduler) -> None:
    await scheduler.shutdown()
    await asyncio.sleep(0)


# ----------------------------------------------------------------------
# run_now
# ----------------------------------------------------------------------

async def test_run_now_runs_action_and_keeps_schedule(store: TaskStore, bus: EventBus) -> None:
    brain = FakeBrain()
    runner = TaskRunner(store=store, bus=bus, agent_brain=brain)
    scheduler = TaskScheduler(store=store, bus=bus, runner=runner)
    tid = await scheduler.schedule(_every(prompt="morning"))
    before = await store.get(tid)
    assert before is not None

    await scheduler.run_now(tid)
    await _settle(scheduler)

    assert brain.calls == ["morning"]
    after = await store.get(tid)
    assert after is not None
    assert after["state"] == "scheduled"
    assert after["due_at_ns"] == before["due_at_ns"], "run-now must not shift the schedule"
    assert any(tid == t for _, t in scheduler._heap), "still armed in the heap"
    events = [s["payload"].get("event") for s in after["steps"] if s["kind"] == "log"]
    assert "run_now" in events
    assert "agent_result" in events
    assert after["finished_at_ns"] is not None
    assert after["last_error"] is None


async def test_run_now_consumes_one_shot_task(store: TaskStore, bus: EventBus) -> None:
    brain = FakeBrain()
    runner = TaskRunner(store=store, bus=bus, agent_brain=brain)
    scheduler = TaskScheduler(store=store, bus=bus, runner=runner)
    spec = TaskSpec(
        title="once",
        trigger=TriggerAfterDelay(delay_seconds=3600),
        action=AgentAction(prompt="once"),
    )
    tid = await scheduler.schedule(spec)

    await scheduler.run_now(tid)
    await _settle(scheduler)

    task = await store.get(tid)
    assert task is not None
    assert task["state"] == "completed"
    assert all(t != tid for _, t in scheduler._heap), "must not fire a second time"


async def test_run_now_refuses_running_and_unknown(store: TaskStore, bus: EventBus) -> None:
    scheduler = TaskScheduler(store=store, bus=bus, runner=RecordingRunner())
    tid = await scheduler.schedule(_every())
    await store.update_state(tid, "running")
    with pytest.raises(TaskStateConflict):
        await scheduler.run_now(tid)
    with pytest.raises(TaskNotFound):
        await scheduler.run_now("nope")


async def test_run_now_restores_paused_state(store: TaskStore, bus: EventBus) -> None:
    brain = FakeBrain()
    runner = TaskRunner(store=store, bus=bus, agent_brain=brain)
    scheduler = TaskScheduler(store=store, bus=bus, runner=runner)
    tid = await scheduler.schedule(_every())
    await scheduler.pause(tid)

    await scheduler.run_now(tid)
    await _settle(scheduler)

    assert brain.calls == ["brief"]
    task = await store.get(tid)
    assert task is not None
    assert task["state"] == "paused", "a manual run must not switch a paused task on"
    assert all(t != tid for _, t in scheduler._heap)


# ----------------------------------------------------------------------
# pause / resume
# ----------------------------------------------------------------------

async def test_pause_removes_from_heap_and_hydrate_leaves_it(
    store: TaskStore, bus: EventBus,
) -> None:
    scheduler = TaskScheduler(store=store, bus=bus, runner=RecordingRunner())
    tid = await scheduler.schedule(_every())
    assert any(t == tid for _, t in scheduler._heap)

    await scheduler.pause(tid)
    await scheduler.pause(tid)  # idempotent

    assert all(t != tid for _, t in scheduler._heap)
    task = await store.get(tid)
    assert task is not None
    assert task["state"] == "paused"
    assert [s["payload"]["event"] for s in task["steps"]] == ["paused"]

    fresh = TaskScheduler(store=store, bus=bus, runner=RecordingRunner())
    await fresh.hydrate()
    assert fresh._heap == []
    assert tid not in fresh._known


async def test_pause_refuses_one_shot_running_and_terminal(
    store: TaskStore, bus: EventBus,
) -> None:
    scheduler = TaskScheduler(store=store, bus=bus, runner=RecordingRunner())
    one_shot = await scheduler.schedule(TaskSpec(
        title="once", trigger=TriggerAfterDelay(delay_seconds=60),
        action=SpeakAction(text="x"),
    ))
    with pytest.raises(TaskStateConflict):
        await scheduler.pause(one_shot)

    tid = await scheduler.schedule(_every())
    await store.update_state(tid, "running")
    with pytest.raises(TaskStateConflict):
        await scheduler.pause(tid)
    await store.update_state(tid, "cancelled")
    with pytest.raises(TaskStateConflict):
        await scheduler.pause(tid)
    with pytest.raises(TaskNotFound):
        await scheduler.pause("nope")


async def test_resume_rearms_unanchored_task_one_interval_out(
    store: TaskStore, bus: EventBus,
) -> None:
    scheduler = TaskScheduler(store=store, bus=bus, runner=RecordingRunner())
    tid = await scheduler.schedule(_every(interval=600))
    await scheduler.pause(tid)

    now_ns = 1_000_000_000_000_000_000
    due = await scheduler.resume(tid, now_ns=now_ns)

    assert due == now_ns + 600 * 10**9
    task = await store.get(tid)
    assert task is not None
    assert task["state"] == "scheduled"
    assert task["due_at_ns"] == due
    assert (due, tid) in scheduler._heap
    assert [s["payload"]["event"] for s in task["steps"]] == ["paused", "resumed"]


async def test_resume_keeps_wall_clock_anchor(store: TaskStore, bus: EventBus) -> None:
    anchor = datetime(2026, 1, 1, 7, 30, tzinfo=UTC)
    spec = _every(interval=86_400, start_at=anchor.isoformat())
    scheduler = TaskScheduler(store=store, bus=bus, runner=RecordingRunner())
    tid = await scheduler.schedule(spec)
    await scheduler.pause(tid)

    now = anchor + timedelta(days=10, hours=3)   # 10:30 on day 10
    due = await scheduler.resume(tid, now_ns=int(now.timestamp() * 1e9))

    expected = anchor + timedelta(days=11)       # next 07:30 strictly after now
    assert due == int(expected.timestamp() * 1e9)


async def test_resume_refuses_non_paused(store: TaskStore, bus: EventBus) -> None:
    scheduler = TaskScheduler(store=store, bus=bus, runner=RecordingRunner())
    tid = await scheduler.schedule(_every())
    with pytest.raises(TaskStateConflict):
        await scheduler.resume(tid)


async def test_pause_resume_on_event_task(store: TaskStore, bus: EventBus) -> None:
    scheduler = TaskScheduler(store=store, bus=bus, runner=RecordingRunner())
    spec = TaskSpec(
        title="rule",
        trigger=TriggerOnEvent(event_name="MissionCompleted", max_firings=None),
        action=SpeakAction(text="done"),
    )
    tid = await scheduler.schedule(spec)
    assert tid in scheduler._on_event_index["MissionCompleted"]

    await scheduler.pause(tid)
    assert tid not in scheduler._on_event_index["MissionCompleted"]

    due = await scheduler.resume(tid)
    assert due is None
    assert tid in scheduler._on_event_index["MissionCompleted"]
    task = await store.get(tid)
    assert task is not None and task["state"] == "scheduled"


# ----------------------------------------------------------------------
# next_every_due_ns
# ----------------------------------------------------------------------

def test_next_every_due_future_anchor_is_used_as_is() -> None:
    anchor = datetime(2030, 1, 1, 8, 0, tzinfo=UTC)
    spec = _every(interval=3600, start_at=anchor.isoformat())
    now_ns = int((anchor - timedelta(days=1)).timestamp() * 1e9)
    assert next_every_due_ns(spec, now_ns) == parse_iso_timestamp_to_ns(anchor.isoformat())


def test_next_every_due_on_grid_is_pushed_to_next_slot() -> None:
    anchor = datetime(2026, 1, 1, 8, 0, tzinfo=UTC)
    spec = _every(interval=3600, start_at=anchor.isoformat())
    now_ns = int((anchor + timedelta(hours=5)).timestamp() * 1e9)  # exactly on a slot
    assert next_every_due_ns(spec, now_ns) == now_ns + 3600 * 10**9


def test_next_every_due_bad_anchor_degrades_to_interval() -> None:
    spec = _every(interval=60, start_at="not-a-timestamp")
    assert next_every_due_ns(spec, 10**18) == 10**18 + 60 * 10**9
