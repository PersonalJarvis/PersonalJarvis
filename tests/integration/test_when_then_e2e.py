"""End-to-end When-Then proof: MissionCompleted on the global bus drives a real
TaskScheduler → real TaskRunner → Computer-Use dispatch + spoken confirmation.

This is the whole feature wired together with the production Scheduler and Runner
(only the harness is a fake) — a regression anywhere in the chain (filter, event
passthrough, templating, notify) fails here.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.events import AnnouncementRequested, MissionCompleted
from jarvis.core.protocols import HarnessResult
from jarvis.tasks.runner import TaskRunner
from jarvis.tasks.scheduler import TaskScheduler
from jarvis.tasks.schema import HarnessDispatchAction, TaskSpec, TriggerOnEvent
from jarvis.tasks.store import TaskStore


class FakeHarnessManager:
    def __init__(self) -> None:
        self.dispatched: list[tuple[str, Any]] = []

    async def dispatch(self, name: str, task: Any):
        self.dispatched.append((name, task))

        async def gen():
            yield HarnessResult(stdout="opened\n", exit_code=0, is_final=True)
        return gen()


@pytest.fixture
async def store(tmp_path: Path):
    s = TaskStore(tmp_path / "e2e.db")
    await s.init()
    try:
        yield s
    finally:
        await s.close()


async def test_mission_finished_opens_browser_and_speaks(store: TaskStore) -> None:
    bus = EventBus()
    harness = FakeHarnessManager()
    runner = TaskRunner(store=store, bus=bus, harness_manager=harness)
    scheduler = TaskScheduler(store=store, bus=bus, runner=runner)
    scheduler.bind_bus()

    announcements: list[AnnouncementRequested] = []

    async def on_ann(e: AnnouncementRequested) -> None:
        announcements.append(e)

    bus.subscribe(AnnouncementRequested, on_ann)

    # The standing When-Then rule the user would build in the dialog.
    rule = TaskSpec(
        title="Mission done → open + notify",
        trigger=TriggerOnEvent(
            event_name="MissionCompleted",
            filter_expr="status == 'approved'",
            max_firings=None,
        ),
        action=HarnessDispatchAction(
            harness="screenshot",
            prompt="Open {result_uri} in the browser.",
            allow_computer_use=True,
        ),
        announce_on_success="Your mission is ready — I opened {result_uri}.",
    )
    await scheduler.schedule(rule)

    # A mission finishes — the bridge would publish exactly this on the global bus.
    await bus.publish(
        MissionCompleted(
            mission_id="run-42",
            status="approved",
            result_uri="/outputs/report.md",
            summary_en="Report written.",
        )
    )
    # The scheduler dispatches the runner fire-and-forget; let it run to completion.
    for _ in range(50):
        await asyncio.sleep(0.01)
        if announcements:
            break

    # Computer-Use was dispatched with the interpolated goal.
    assert len(harness.dispatched) == 1
    name, task = harness.dispatched[0]
    assert name == "screenshot"
    assert task.prompt == "Open /outputs/report.md in the browser."
    assert task.allow_computer_use is True

    # And Jarvis confirmed it (the post-hangup-capable readback kind).
    assert len(announcements) == 1
    assert announcements[0].text == "Your mission is ready — I opened /outputs/report.md."
    assert announcements[0].kind == "subagent"

    # The task itself completed.
    task_row = await store.get(str(rule.id))
    assert task_row is not None
    assert task_row["state"] == "completed"


async def test_failed_mission_does_not_fire_an_approved_rule(store: TaskStore) -> None:
    bus = EventBus()
    harness = FakeHarnessManager()
    runner = TaskRunner(store=store, bus=bus, harness_manager=harness)
    scheduler = TaskScheduler(store=store, bus=bus, runner=runner)
    scheduler.bind_bus()

    rule = TaskSpec(
        title="only on success",
        trigger=TriggerOnEvent(event_name="MissionCompleted",
                               filter_expr="status == 'approved'", max_firings=None),
        action=HarnessDispatchAction(harness="screenshot", prompt="x",
                                     allow_computer_use=True),
    )
    await scheduler.schedule(rule)

    await bus.publish(MissionCompleted(mission_id="run-7", status="failed"))
    await asyncio.sleep(0.1)

    assert harness.dispatched == []
