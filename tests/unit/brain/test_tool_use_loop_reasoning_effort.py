"""Reasoning-effort hint on delegated tool-loop rounds (latency guard).

Forensic origin (FlightRecorder 2026-07-17): delegated realtime voice turns
ran p50 15-18 s / worst 33 s because the hoisted Tool Model (a
thinking-by-default Gemini Flash) spent seconds of internal reasoning on
EVERY one of its 3-6 sequential tool-loop rounds. The router-tier factory
caps thinking only on the tier's own provider entry, so a live provider
switch parks the cap on the wrong entry and the hoisted Tool Model escapes
it. The per-request ``reasoning_effort="none"`` hint closes that hole
deterministically (same doctrine as the Computer-Use calls).

Contract under test:
  1. ``ToolUseLoop(reasoning_effort="none")`` stamps the hint onto EVERY
     per-round BrainRequest — tool rounds, the final answer round, and the
     deadline-forced tool-less round alike.
  2. The default (``None``) keeps the provider default on every round —
     classic chat turns are unchanged.
  3. ``BrainDispatcher`` forwards the hint into the loop it builds, and
     onto the simple no-tools request path.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from jarvis.brain.dispatcher import BrainDispatcher
from jarvis.brain.tool_use_loop import ToolUseLoop
from jarvis.core.protocols import BrainDelta, BrainRequest, ToolResult


class _ListTool:
    name = "wiki-list"
    schema: dict[str, Any] = {}


class _ExecOK:
    async def execute(self, tool: Any, args: dict[str, Any], **_: Any) -> ToolResult:
        return ToolResult(success=True, output="- log.md")


class _OneToolThenAnswerBrain:
    """One tool round, then a normal answer — records every request."""

    def __init__(self) -> None:
        self.requests: list[BrainRequest] = []

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.requests.append(req)
        if len(self.requests) == 1 and req.tools:
            yield BrainDelta(tool_call={"id": "c1", "name": "wiki-list", "input": {}})
            yield BrainDelta(finish_reason="tool_use")
            return
        yield BrainDelta(content="Answer.")
        yield BrainDelta(finish_reason="stop")


class _GreedyBrain:
    """Calls a tool whenever tools are offered (forces the deadline round)."""

    def __init__(self) -> None:
        self.requests: list[BrainRequest] = []

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.requests.append(req)
        if req.tools:
            yield BrainDelta(
                tool_call={
                    "id": f"c{len(self.requests)}",
                    "name": "wiki-list",
                    "input": {},
                }
            )
            yield BrainDelta(finish_reason="tool_use")
            return
        yield BrainDelta(content="Grounded answer.")
        yield BrainDelta(finish_reason="stop")


@pytest.mark.asyncio
async def test_reasoning_effort_none_reaches_every_round() -> None:
    brain = _OneToolThenAnswerBrain()
    loop = ToolUseLoop(
        brain,
        {"wiki-list": _ListTool()},
        _ExecOK(),  # type: ignore[arg-type]
        reasoning_effort="none",
    )

    await loop.run([], user_utterance="what is in my wiki")

    assert len(brain.requests) == 2
    assert all(req.reasoning_effort == "none" for req in brain.requests)


@pytest.mark.asyncio
async def test_reasoning_effort_none_survives_deadline_forced_round() -> None:
    brain = _GreedyBrain()
    loop = ToolUseLoop(
        brain,
        {"wiki-list": _ListTool()},
        _ExecOK(),  # type: ignore[arg-type]
        deadline_s=0.0,  # expires after the first tool round
        reasoning_effort="none",
    )

    await loop.run([], user_utterance="what is in my wiki")

    # Tool round + the forced tool-less final round: both carry the hint.
    assert len(brain.requests) == 2
    assert not brain.requests[1].tools
    assert all(req.reasoning_effort == "none" for req in brain.requests)


@pytest.mark.asyncio
async def test_default_keeps_provider_default_reasoning() -> None:
    brain = _OneToolThenAnswerBrain()
    loop = ToolUseLoop(
        brain,
        {"wiki-list": _ListTool()},
        _ExecOK(),  # type: ignore[arg-type]
    )

    await loop.run([], user_utterance="what is in my wiki")

    assert brain.requests
    assert all(req.reasoning_effort is None for req in brain.requests)


@pytest.mark.asyncio
async def test_dispatcher_forwards_reasoning_effort_into_tool_loop() -> None:
    brain = _OneToolThenAnswerBrain()
    dispatcher = BrainDispatcher(
        brain,  # type: ignore[arg-type]
        tools={"wiki-list": _ListTool()},  # type: ignore[dict-item]
        executor=_ExecOK(),  # type: ignore[arg-type]
        reasoning_effort="none",
    )

    await dispatcher.dispatch("what is in my wiki")

    assert brain.requests
    assert all(req.reasoning_effort == "none" for req in brain.requests)


@pytest.mark.asyncio
async def test_dispatcher_forwards_reasoning_effort_on_simple_path() -> None:
    brain = _OneToolThenAnswerBrain()
    dispatcher = BrainDispatcher(
        brain,  # type: ignore[arg-type]
        reasoning_effort="none",
    )

    await dispatcher.dispatch("hello there")

    assert len(brain.requests) == 1
    assert brain.requests[0].reasoning_effort == "none"
