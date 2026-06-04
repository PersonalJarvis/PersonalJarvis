"""Unit-Tests fuer TaskRunner — SpeakAction, ToolCallAction, HarnessDispatch."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from jarvis.control.cancel import CancelToken
from jarvis.core.bus import EventBus
from jarvis.core.events import TaskCompleted, TaskFailed, TaskStarted
from jarvis.core.protocols import HarnessResult, ToolResult
from jarvis.tasks.runner import TaskRunner
from jarvis.tasks.schema import (
    HarnessDispatchAction,
    SpeakAction,
    TaskSpec,
    ToolCallAction,
    TriggerAfterDelay,
)
from jarvis.tasks.store import TaskStore

# ----------------------------------------------------------------------
# Fakes
# ----------------------------------------------------------------------

class FakeTTS:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def synthesize(self, text: str, voice: str | None = None):
        self.calls.append(text)

        async def gen():
            yield b"chunk1"
            yield b"chunk2"
        return gen()


class FakeHarnessManager:
    def __init__(self, results: list[HarnessResult] | None = None) -> None:
        self.dispatched: list[tuple[str, Any]] = []
        self._results = results or [
            HarnessResult(stdout="hi\n", exit_code=0, is_final=True),
        ]

    async def dispatch(self, name: str, task: Any):
        self.dispatched.append((name, task))

        async def gen():
            for r in self._results:
                yield r
        return gen()


class FakeTool:
    name = "whoami"
    schema: dict[str, Any] = {}
    description = "test"
    risk_tier = "safe"

    async def execute(self, args: dict[str, Any], ctx: Any) -> ToolResult:  # pragma: no cover
        return ToolResult(success=True, output={"user": "admin"})


class FakeExecutor:
    def __init__(self, success: bool = True) -> None:
        self.called: list[tuple[str, dict[str, Any]]] = []
        self._success = success

    async def execute(self, tool: Any, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        self.called.append((tool.name, args))
        if not self._success:
            return ToolResult(success=False, output=None, error="boom")
        return ToolResult(success=True, output={"ok": True})


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------

@pytest.fixture
async def store(tmp_path: Path):
    s = TaskStore(tmp_path / "runner.db")
    await s.init()
    try:
        yield s
    finally:
        await s.close()


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


# ----------------------------------------------------------------------
# SpeakAction
# ----------------------------------------------------------------------

async def test_runner_speak_action_calls_tts(store: TaskStore, bus: EventBus) -> None:
    tts = FakeTTS()
    runner = TaskRunner(store=store, bus=bus, tts=tts)

    completed: list[TaskCompleted] = []
    bus.subscribe(TaskCompleted, lambda e: _append(completed, e))

    spec = TaskSpec(
        title="say",
        trigger=TriggerAfterDelay(delay_seconds=1.0),
        action=SpeakAction(text="Hallo Welt"),
    )
    tid = await store.insert(spec)

    await runner.run(tid)

    assert tts.calls == ["Hallo Welt"]

    task = await store.get(tid)
    assert task is not None
    assert task["state"] == "completed"
    # speak produziert: action-row + log-row
    kinds = [s["kind"] for s in task["steps"]]
    assert "action" in kinds
    assert "log" in kinds
    assert len(completed) == 1


# ----------------------------------------------------------------------
# HarnessDispatchAction
# ----------------------------------------------------------------------

async def test_runner_harness_dispatch(store: TaskStore, bus: EventBus) -> None:
    hm = FakeHarnessManager()
    runner = TaskRunner(store=store, bus=bus, harness_manager=hm)

    spec = TaskSpec(
        title="code",
        trigger=TriggerAfterDelay(delay_seconds=1.0),
        action=HarnessDispatchAction(harness="openclaw", prompt="write hello"),
    )
    tid = await store.insert(spec)

    await runner.run(tid)

    assert len(hm.dispatched) == 1
    assert hm.dispatched[0][0] == "openclaw"

    task = await store.get(tid)
    assert task is not None
    assert task["state"] == "completed"


async def test_runner_harness_exit_code_nonzero_fails(store: TaskStore, bus: EventBus) -> None:
    hm = FakeHarnessManager(results=[
        HarnessResult(stdout="", stderr="error!", exit_code=1, is_final=True),
    ])
    runner = TaskRunner(store=store, bus=bus, harness_manager=hm)

    failed: list[TaskFailed] = []
    bus.subscribe(TaskFailed, lambda e: _append(failed, e))

    spec = TaskSpec(
        title="bad",
        trigger=TriggerAfterDelay(delay_seconds=1.0),
        action=HarnessDispatchAction(harness="codex", prompt="boom"),
    )
    tid = await store.insert(spec)

    await runner.run(tid)

    task = await store.get(tid)
    assert task is not None
    assert task["state"] == "failed"
    assert task["last_error"] is not None
    assert len(failed) == 1


# ----------------------------------------------------------------------
# ToolCallAction
# ----------------------------------------------------------------------

async def test_runner_tool_call(store: TaskStore, bus: EventBus) -> None:
    tool = FakeTool()
    executor = FakeExecutor()
    runner = TaskRunner(
        store=store,
        bus=bus,
        tool_executor=executor,
        tool_registry={"whoami": tool},
    )

    spec = TaskSpec(
        title="tool",
        trigger=TriggerAfterDelay(delay_seconds=1.0),
        action=ToolCallAction(tool_name="whoami", args={"hello": 1}),
    )
    tid = await store.insert(spec)

    await runner.run(tid)

    assert executor.called == [("whoami", {"hello": 1})]
    task = await store.get(tid)
    assert task is not None
    assert task["state"] == "completed"


async def test_runner_tool_missing_registry_fails(store: TaskStore, bus: EventBus) -> None:
    runner = TaskRunner(store=store, bus=bus)

    spec = TaskSpec(
        title="tool-missing",
        trigger=TriggerAfterDelay(delay_seconds=1.0),
        action=ToolCallAction(tool_name="whoami", args={}),
    )
    tid = await store.insert(spec)

    await runner.run(tid)

    task = await store.get(tid)
    assert task is not None
    assert task["state"] == "failed"


async def test_runner_tool_failure_recorded(store: TaskStore, bus: EventBus) -> None:
    tool = FakeTool()
    executor = FakeExecutor(success=False)
    runner = TaskRunner(
        store=store,
        bus=bus,
        tool_executor=executor,
        tool_registry={"whoami": tool},
    )
    spec = TaskSpec(
        title="tool-fail",
        trigger=TriggerAfterDelay(delay_seconds=1.0),
        action=ToolCallAction(tool_name="whoami", args={}),
    )
    tid = await store.insert(spec)

    await runner.run(tid)

    task = await store.get(tid)
    assert task is not None
    assert task["state"] == "failed"
    assert "boom" in (task["last_error"] or "")


# ----------------------------------------------------------------------
# Cancel
# ----------------------------------------------------------------------

async def test_runner_respects_cancel_token(store: TaskStore, bus: EventBus) -> None:
    tts = FakeTTS()
    runner = TaskRunner(store=store, bus=bus, tts=tts)

    spec = TaskSpec(
        title="nope",
        trigger=TriggerAfterDelay(delay_seconds=1.0),
        action=SpeakAction(text="hi"),
    )
    tid = await store.insert(spec)

    token = CancelToken()
    token.cancel("user_stop")

    await runner.run(tid, cancel_token=token)

    # TTS wurde nicht aufgerufen
    assert tts.calls == []
    task = await store.get(tid)
    assert task is not None
    assert task["state"] == "cancelled"


async def test_runner_emits_task_started(store: TaskStore, bus: EventBus) -> None:
    tts = FakeTTS()
    runner = TaskRunner(store=store, bus=bus, tts=tts)

    started: list[TaskStarted] = []
    bus.subscribe(TaskStarted, lambda e: _append(started, e))

    spec = TaskSpec(
        title="t",
        trigger=TriggerAfterDelay(delay_seconds=1.0),
        action=SpeakAction(text="x"),
    )
    tid = await store.insert(spec)

    await runner.run(tid)

    assert len(started) == 1
    assert started[0].task_id == tid


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

async def _append(target: list[Any], evt: Any) -> None:
    target.append(evt)
