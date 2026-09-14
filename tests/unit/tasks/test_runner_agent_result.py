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
from jarvis.core.events import AnnouncementRequested, TaskCompleted
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


def _collect(bus: EventBus, event_type: type) -> list:
    got: list = []

    async def _on(event) -> None:
        got.append(event)

    bus.subscribe(event_type, _on)
    return got


async def test_rub95_agent_routine_completion_requests_no_automatic_speech(
    store: TaskStore,
) -> None:
    """RUB-95: routine completion -> result processing -> no automatic speech.

    The agent result must stay visible (result sink + agent_result step +
    TaskCompleted) while no AnnouncementRequested is emitted, with or without
    an active voice session (no request means nothing can start speaking).
    """
    delivered: list[tuple[tuple[str, ...], str, str]] = []

    async def sink(tags: tuple[str, ...], text: str, status: str) -> None:
        delivered.append((tags, text, status))

    bus = EventBus()
    announcements = _collect(bus, AnnouncementRequested)
    completions = _collect(bus, TaskCompleted)
    runner = TaskRunner(store, bus, agent_brain=FakeAgentBrain(), result_sink=sink)
    spec = TaskSpec(
        title="[agent:Mailbox] Inbox sweep",
        trigger=TriggerAfterDelay(delay_seconds=0.01),
        action=AgentAction(prompt="Sort the inbox."),
        tags=("society", "agent:mailbox"),
    )
    task_id = await store.insert(spec)
    await asyncio.wait_for(runner.run(task_id, CancelToken()), timeout=2.0)

    assert announcements == []
    assert delivered == [(("society", "agent:mailbox"), "Inbox sorted: 3 replies drafted.", "done")]
    assert len(completions) == 1 and completions[0].task_id == task_id
    row = await store.get(task_id)
    assert row is not None and row["state"] == "completed"
    agent_steps = [
        s
        for s in row["steps"]
        if s["kind"] == "log" and s["payload"].get("event") == "agent_result"
    ]
    assert len(agent_steps) == 1
    assert "Inbox sorted" in agent_steps[0]["payload"]["text"]


async def test_rub95_owned_society_routine_completion_stays_silent(
    store: TaskStore,
) -> None:
    """RUB-95 owned path: run_owned_routine answer must not trigger speech."""

    async def owned_runner(task_id: str, tags: tuple[str, ...], prompt: str, cancel: Any):
        assert tags == ("society", "agent:mailbox")
        return "Routine answer from the owner's chat."

    bus = EventBus()
    announcements = _collect(bus, AnnouncementRequested)
    runner = TaskRunner(store, bus, owned_agent_runner=owned_runner)
    spec = TaskSpec(
        title="[agent:Mailbox] Owned sweep",
        trigger=TriggerAfterDelay(delay_seconds=0.01),
        action=AgentAction(prompt="Sort the inbox."),
        tags=("society", "agent:mailbox"),
    )
    task_id = await store.insert(spec)
    await asyncio.wait_for(runner.run(task_id, CancelToken()), timeout=2.0)

    assert announcements == []
    row = await store.get(task_id)
    assert row is not None and row["state"] == "completed"
    agent_steps = [
        s
        for s in row["steps"]
        if s["kind"] == "log" and s["payload"].get("event") == "agent_result"
    ]
    assert len(agent_steps) == 1
    assert "Routine answer" in agent_steps[0]["payload"]["text"]


async def test_rub95_explicit_announce_on_success_still_speaks_once(
    store: TaskStore,
) -> None:
    """Explicit opt-in read-aloud keeps working — exactly once, templated."""
    bus = EventBus()
    announcements = _collect(bus, AnnouncementRequested)
    runner = TaskRunner(store, bus, agent_brain=FakeAgentBrain())
    spec = TaskSpec(
        title="[agent:Mailbox] Inbox sweep",
        trigger=TriggerAfterDelay(delay_seconds=0.01),
        action=AgentAction(prompt="Sort the inbox."),
        tags=("society", "agent:mailbox"),
        announce_on_success="Inbox sweep done.",
    )
    task_id = await store.insert(spec)
    await asyncio.wait_for(runner.run(task_id, CancelToken()), timeout=2.0)

    assert len(announcements) == 1
    assert announcements[0].text == "Inbox sweep done."
    assert announcements[0].kind == "subagent"
