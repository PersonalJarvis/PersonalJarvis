"""Scheduled agent runs that find no billable capacity are skipped, not billed.

The brain raises a :class:`CapacityDeferred` when an unattended turn has no
subscription or local model left (see
``tests/unit/brain/test_run_task_unattended_policy.py``). The runner records
the skip, keeps a recurring task on its schedule, and the "Run now" routes mark
their run as started by the person while a scheduled firing stays unattended.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from jarvis.brain.background_policy import BackgroundDeferred
from jarvis.control.cancel import CancelToken
from jarvis.core.bus import EventBus
from jarvis.core.protocols import current_started_by_user
from jarvis.tasks.runner import TaskRunner
from jarvis.tasks.scheduler import TaskScheduler
from jarvis.tasks.schema import AgentAction, TaskSpec, TriggerAfterDelay, TriggerEvery
from jarvis.tasks.store import TaskStore
from jarvis.ui.web import tasks_routes, workflows_routes


class DeferringBrain:
    """Raises what the real brain raises when only per-token keys remain."""

    def __init__(self) -> None:
        self.started_by_user: list[bool] = []

    async def run_task(self, **_: Any) -> str:
        self.started_by_user.append(current_started_by_user.get())
        raise BackgroundDeferred(
            "No subscription or local model is available for this unattended run; "
            "a paid API key is not used without your approval."
        )


class RecordingBrain:
    def __init__(self) -> None:
        self.started_by_user: list[bool] = []

    async def run_task(self, **_: Any) -> str:
        self.started_by_user.append(current_started_by_user.get())
        return "done"


@pytest.fixture
async def store(tmp_path: Path):
    s = TaskStore(tmp_path / "runner.db")
    await s.init()
    try:
        yield s
    finally:
        await s.close()


def _events(row: dict[str, Any]) -> list[dict[str, Any]]:
    return [step["payload"] for step in row["steps"]]


async def test_a_deferred_recurring_run_is_skipped_and_stays_scheduled(
    store: TaskStore,
) -> None:
    runner = TaskRunner(store, EventBus(), agent_brain=DeferringBrain())
    task_id = await store.insert(
        TaskSpec(
            title="Morning digest",
            trigger=TriggerEvery(interval_seconds=86400),
            action=AgentAction(prompt="Summarise my day."),
        )
    )
    await asyncio.wait_for(runner.run(task_id, CancelToken()), timeout=2.0)

    row = await store.get(task_id)
    assert row is not None
    assert row["state"] == "scheduled"  # tries again at its next occurrence
    assert "paid API key is not used" in (row["last_error"] or "")
    skipped = [e for e in _events(row) if e.get("event") == "skipped"]
    assert skipped and skipped[0]["reason"] == "waiting_capacity"


async def test_a_deferred_one_shot_run_fails_with_the_reason(store: TaskStore) -> None:
    runner = TaskRunner(store, EventBus(), agent_brain=DeferringBrain())
    task_id = await store.insert(
        TaskSpec(
            title="Once",
            trigger=TriggerAfterDelay(delay_seconds=0.01),
            action=AgentAction(prompt="Summarise my day."),
        )
    )
    await asyncio.wait_for(runner.run(task_id, CancelToken()), timeout=2.0)

    row = await store.get(task_id)
    assert row is not None
    assert row["state"] == "failed"
    assert "paid API key is not used" in (row["last_error"] or "")
    assert any(e.get("event") == "skipped" for e in _events(row))


async def test_a_scheduled_firing_is_unattended(store: TaskStore) -> None:
    brain = RecordingBrain()
    runner = TaskRunner(store, EventBus(), agent_brain=brain)
    task_id = await store.insert(
        TaskSpec(
            title="Once",
            trigger=TriggerAfterDelay(delay_seconds=0.01),
            action=AgentAction(prompt="hi"),
        )
    )
    await asyncio.wait_for(runner.run(task_id, CancelToken()), timeout=2.0)
    assert brain.started_by_user == [False]


async def test_run_now_marks_the_run_as_started_by_the_person(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "routes.db")
    await store.init()
    bus = EventBus()
    brain = RecordingBrain()
    scheduler = TaskScheduler(
        store=store, bus=bus, runner=TaskRunner(store, bus, agent_brain=brain)
    )
    app = FastAPI()
    app.include_router(tasks_routes.router)
    app.state.task_store = store
    app.state.task_scheduler = scheduler
    try:
        task_id = await store.insert(
            TaskSpec(
                title="Digest",
                trigger=TriggerEvery(interval_seconds=86400),
                action=AgentAction(prompt="hi"),
            )
        )
        await store.update_state(task_id, "scheduled")
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            res = await client.post(f"/api/tasks/{task_id}/run")
        assert res.status_code == 200
        await scheduler.shutdown()
        assert brain.started_by_user == [True]
        assert current_started_by_user.get() is False  # the marker never leaks out
    finally:
        await scheduler.shutdown()
        await store.close()


async def test_manual_workflow_run_is_started_by_the_person() -> None:
    seen: list[tuple[str, bool]] = []

    class _Runner:
        async def trigger(self, workflow_id: str, *, trigger_reason: str, **_: Any) -> str:
            seen.append((trigger_reason, current_started_by_user.get()))
            return "run-1"

    app = FastAPI()
    app.include_router(workflows_routes.router)
    app.state.workflow_store = object()
    app.state.workflow_runner = _Runner()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        res = await client.post("/api/workflows/wf-1/run", json={})
    assert res.status_code == 200
    assert seen == [("manual", True)]
