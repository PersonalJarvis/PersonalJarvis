"""TaskScheduler When-Then behaviour — event passthrough + fire-once dedup.

The scheduler must hand the triggering event's flat fields to the runner as
``trigger_event`` and must never fire the same standing rule twice for the same
event subject (one mission's terminal event).
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.events import MissionCompleted
from jarvis.tasks.scheduler import TaskScheduler
from jarvis.tasks.schema import SpeakAction, TaskSpec, TriggerOnEvent
from jarvis.tasks.store import TaskStore


class CapturingRunner:
    """Records every ``run()`` call with its ``trigger_event`` context."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    async def run(
        self,
        task_id: str,
        cancel_token: Any = None,
        *,
        trigger_event: dict[str, Any] | None = None,
    ) -> None:
        self.calls.append((task_id, trigger_event))


@pytest.fixture
async def store(tmp_path: Path):
    s = TaskStore(tmp_path / "sched-wt.db")
    await s.init()
    try:
        yield s
    finally:
        await s.close()


async def _standing_rule(store: TaskStore, scheduler: TaskScheduler) -> str:
    spec = TaskSpec(
        title="whenever a mission finishes",
        trigger=TriggerOnEvent(
            event_name="MissionCompleted",
            filter_expr="status == 'approved'",
            max_firings=None,  # standing rule: fires for every matching mission
        ),
        action=SpeakAction(text="done"),
    )
    return await scheduler.schedule(spec)


async def test_event_fields_passthrough_to_runner(store: TaskStore) -> None:
    bus = EventBus()
    cap = CapturingRunner()
    scheduler = TaskScheduler(store=store, bus=bus, runner=cap)
    scheduler.bind_bus()
    await _standing_rule(store, scheduler)

    await bus.publish(
        MissionCompleted(mission_id="m1", status="approved", result_uri="/out/r.md")
    )
    await asyncio.sleep(0.05)

    assert len(cap.calls) == 1
    _tid, ctx = cap.calls[0]
    assert ctx is not None
    assert ctx["result_uri"] == "/out/r.md"
    assert ctx["status"] == "approved"
    assert ctx["mission_id"] == "m1"


async def test_dedup_same_mission_fires_once(store: TaskStore) -> None:
    bus = EventBus()
    cap = CapturingRunner()
    scheduler = TaskScheduler(store=store, bus=bus, runner=cap)
    scheduler.bind_bus()
    await _standing_rule(store, scheduler)

    # Same mission re-published — must fire exactly once.
    await bus.publish(MissionCompleted(mission_id="m1", status="approved"))
    await bus.publish(MissionCompleted(mission_id="m1", status="approved"))
    await asyncio.sleep(0.05)
    assert len(cap.calls) == 1

    # A different mission is a distinct subject — fires again.
    await bus.publish(MissionCompleted(mission_id="m2", status="approved"))
    await asyncio.sleep(0.05)
    assert len(cap.calls) == 2


async def test_filter_blocks_non_approved(store: TaskStore) -> None:
    bus = EventBus()
    cap = CapturingRunner()
    scheduler = TaskScheduler(store=store, bus=bus, runner=cap)
    scheduler.bind_bus()
    await _standing_rule(store, scheduler)

    await bus.publish(MissionCompleted(mission_id="m1", status="failed"))
    await asyncio.sleep(0.05)
    assert cap.calls == []
