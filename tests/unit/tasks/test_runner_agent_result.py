"""An agent action's result reaches the runner's result sink with the task's
tags, so a tagged owner (a society agent's routine) can hear about it in its
own chat. The sink is optional and a failing sink never fails the task."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from jarvis.control.cancel import CancelToken
from jarvis.core.bus import EventBus
from jarvis.tasks.runner import TaskRunner
from jarvis.tasks.schema import AgentAction, TaskSpec, TriggerAfterDelay
from jarvis.tasks.store import TaskStore


class FakeAgentBrain:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def run_task(self, *, prompt: str, allowed_tools: Any, model_tier: Any, trace_id: Any):
        self.prompts.append(prompt)
        return "Inbox sorted: 3 replies drafted."


@pytest.fixture
async def store(tmp_path: Path):
    s = TaskStore(tmp_path / "runner.db")
    await s.init()
    try:
        yield s
    finally:
        await s.close()


async def _run(store: TaskStore, runner: TaskRunner, spec: TaskSpec) -> None:
    task_id = await store.insert(spec)
    await asyncio.wait_for(runner.run(task_id, CancelToken()), timeout=2.0)


async def test_an_agent_routine_result_reaches_its_sink(store: TaskStore) -> None:
    delivered: list[tuple[tuple[str, ...], str, str]] = []

    async def sink(tags: tuple[str, ...], text: str, status: str) -> None:
        delivered.append((tags, text, status))

    runner = TaskRunner(store, EventBus(), agent_brain=FakeAgentBrain(), result_sink=sink)
    spec = TaskSpec(
        title="[agent:Mailbox] Inbox sweep",
        trigger=TriggerAfterDelay(delay_seconds=0.01),
        action=AgentAction(prompt="Sort the inbox."),
        tags=("society", "agent:mailbox"),
    )
    await _run(store, runner, spec)
    assert delivered == [(("society", "agent:mailbox"), "Inbox sorted: 3 replies drafted.", "done")]


async def test_an_untagged_task_and_a_missing_sink_change_nothing(store: TaskStore) -> None:
    calls: list[Any] = []

    async def sink(tags: tuple[str, ...], text: str, status: str) -> None:
        calls.append(tags)

    runner = TaskRunner(store, EventBus(), agent_brain=FakeAgentBrain(), result_sink=sink)
    untagged = TaskSpec(
        title="plain",
        trigger=TriggerAfterDelay(delay_seconds=0.01),
        action=AgentAction(prompt="Say hi."),
    )
    await _run(store, runner, untagged)
    assert calls == []
    plain = TaskRunner(store, EventBus(), agent_brain=FakeAgentBrain())
    tagged = TaskSpec(
        title="[agent:Mailbox] no sink",
        trigger=TriggerAfterDelay(delay_seconds=0.01),
        action=AgentAction(prompt="Say hi."),
        tags=("society", "agent:mailbox"),
    )
    await _run(store, plain, tagged)


async def test_a_failing_sink_never_fails_the_task(store: TaskStore, caplog) -> None:
    async def sink(tags: tuple[str, ...], text: str, status: str) -> None:
        raise RuntimeError("chat is down")

    runner = TaskRunner(store, EventBus(), agent_brain=FakeAgentBrain(), result_sink=sink)
    spec = TaskSpec(
        title="[agent:Mailbox] Inbox sweep",
        trigger=TriggerAfterDelay(delay_seconds=0.01),
        action=AgentAction(prompt="Sort the inbox."),
        tags=("society", "agent:mailbox"),
    )
    task_id = await store.insert(spec)
    await asyncio.wait_for(runner.run(task_id, CancelToken()), timeout=2.0)
    row = await store.get(task_id)
    assert row is not None and row["state"] == "completed"
    assert any("result sink failed" in r.getMessage() for r in caplog.records)
