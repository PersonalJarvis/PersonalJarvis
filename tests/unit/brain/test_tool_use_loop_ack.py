"""Tests for the perceived-latency acknowledgment hook in ToolUseLoop.

The loop accepts an ``ack_emitter`` callback. The contract under test:

* called exactly once per turn, with the first scheduled tool call's
  name + input
* not called when the brain produces only text (no tool_calls)
* not called more than once even when the loop iterates multiple times
  (multi-step plans: ack fires on the first iteration's first tool, not
  on every iteration)
* called *before* the tool actually executes (timing matters — the user
  hears the ack while the tool runs in parallel)
* an exception from the emitter is logged but does not block tool execution
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from jarvis.brain.tool_use_loop import ToolUseLoop
from jarvis.core.protocols import BrainDelta, BrainRequest, ToolResult


class _Tool:
    name = "open_app"
    schema: dict[str, Any] = {}


class _RecordingExecutor:
    """Records the order in which execute() is invoked, so we can assert that
    the ack emitter fires *before* the first tool execution."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, dict[str, Any]]] = []

    async def execute(
        self, tool: Any, args: dict[str, Any], **_: Any
    ) -> ToolResult:
        self.calls.append((tool, args))
        return ToolResult(success=True, output="ok")


class _SingleToolBrain:
    """Brain that emits one tool call on iteration 1, then plain text."""

    def __init__(self) -> None:
        self.requests: list[BrainRequest] = []

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.requests.append(req)
        if len(self.requests) == 1:
            yield BrainDelta(
                tool_call={
                    "id": "call_1",
                    "name": "open_app",
                    "input": {"app": "Notepad"},
                }
            )
            yield BrainDelta(finish_reason="tool_use")
            return
        yield BrainDelta(content="Notepad ist offen.")
        yield BrainDelta(finish_reason="stop")


class _TextOnlyBrain:
    """Brain that never calls a tool — the trivial path."""

    def __init__(self) -> None:
        self.requests: list[BrainRequest] = []

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.requests.append(req)
        yield BrainDelta(content="Hallo, was kann ich tun?")  # i18n-allow: simulated German assistant output under test
        yield BrainDelta(finish_reason="stop")


class _MultiStepBrain:
    """Brain that issues tool calls on TWO iterations (multi-step plan)."""

    def __init__(self) -> None:
        self.requests: list[BrainRequest] = []

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.requests.append(req)
        if len(self.requests) == 1:
            yield BrainDelta(
                tool_call={
                    "id": "call_1",
                    "name": "open_app",
                    "input": {"app": "Notepad"},
                }
            )
            yield BrainDelta(finish_reason="tool_use")
            return
        if len(self.requests) == 2:
            yield BrainDelta(
                tool_call={
                    "id": "call_2",
                    "name": "open_app",
                    "input": {"app": "Calculator"},
                }
            )
            yield BrainDelta(finish_reason="tool_use")
            return
        yield BrainDelta(content="Beide Apps sind offen.")
        yield BrainDelta(finish_reason="stop")


@pytest.mark.asyncio
async def test_ack_emitter_called_once_with_first_tool() -> None:
    captured: list[tuple[str, dict[str, Any]]] = []

    async def emitter(name: str, args: dict[str, Any]) -> None:
        captured.append((name, args))

    brain = _SingleToolBrain()
    executor = _RecordingExecutor()
    loop = ToolUseLoop(brain, {"open_app": _Tool()}, executor)  # type: ignore[arg-type]

    await loop.run([], user_utterance="oeffne notepad", ack_emitter=emitter)

    assert captured == [("open_app", {"app": "Notepad"})]
    assert len(executor.calls) == 1


@pytest.mark.asyncio
async def test_ack_emitter_not_called_for_text_only_response() -> None:
    captured: list[tuple[str, dict[str, Any]]] = []

    async def emitter(name: str, args: dict[str, Any]) -> None:
        captured.append((name, args))

    brain = _TextOnlyBrain()
    loop = ToolUseLoop(brain, {}, _RecordingExecutor())  # type: ignore[arg-type]

    await loop.run([], user_utterance="hallo", ack_emitter=emitter)

    assert captured == []


@pytest.mark.asyncio
async def test_ack_emitter_fires_only_once_in_multi_step_plan() -> None:
    captured: list[tuple[str, dict[str, Any]]] = []

    async def emitter(name: str, args: dict[str, Any]) -> None:
        captured.append((name, args))

    brain = _MultiStepBrain()
    executor = _RecordingExecutor()
    loop = ToolUseLoop(brain, {"open_app": _Tool()}, executor)  # type: ignore[arg-type]

    await loop.run([], user_utterance="oeffne beides", ack_emitter=emitter)

    # Two tool executions across two iterations; ack only fires on the first.
    assert len(executor.calls) == 2
    assert captured == [("open_app", {"app": "Notepad"})]


@pytest.mark.asyncio
async def test_ack_emitter_fires_before_first_tool_execution() -> None:
    """Timing test: emitter must run before the executor sees its first call,
    otherwise the perceived-latency win is lost."""
    timeline: list[str] = []

    async def emitter(name: str, args: dict[str, Any]) -> None:
        timeline.append(f"ack:{name}")

    class _OrderingExecutor:
        async def execute(
            self, tool: Any, args: dict[str, Any], **_: Any
        ) -> ToolResult:
            timeline.append(f"exec:{tool.name}")
            return ToolResult(success=True, output="ok")

    brain = _SingleToolBrain()
    loop = ToolUseLoop(
        brain, {"open_app": _Tool()}, _OrderingExecutor()  # type: ignore[arg-type]
    )

    await loop.run([], user_utterance="oeffne notepad", ack_emitter=emitter)

    assert timeline == ["ack:open_app", "exec:open_app"]


@pytest.mark.asyncio
async def test_emitter_exception_does_not_block_tool_execution() -> None:
    async def boom(_name: str, _args: dict[str, Any]) -> None:
        raise RuntimeError("emitter exploded")

    brain = _SingleToolBrain()
    executor = _RecordingExecutor()
    loop = ToolUseLoop(brain, {"open_app": _Tool()}, executor)  # type: ignore[arg-type]

    # Must complete without re-raising.
    await loop.run([], user_utterance="oeffne notepad", ack_emitter=boom)

    # Tool execution still happened.
    assert len(executor.calls) == 1


@pytest.mark.asyncio
async def test_emitter_param_is_optional_default_none() -> None:
    """Backwards-compatibility: legacy call sites that don't pass ack_emitter
    must keep working unchanged."""
    brain = _SingleToolBrain()
    executor = _RecordingExecutor()
    loop = ToolUseLoop(brain, {"open_app": _Tool()}, executor)  # type: ignore[arg-type]

    result = await loop.run([], user_utterance="oeffne notepad")

    assert result.text == "Notepad ist offen."
    assert len(executor.calls) == 1
