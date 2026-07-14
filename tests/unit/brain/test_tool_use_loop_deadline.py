"""Wall-clock deadline for the tool-use loop (delegated realtime turns).

Forensic origin (voice session 2026-07-14 09:29): a delegated "what is
in my wiki" turn ran ~14 sequential LLM rounds over 66 s — the iteration
budget (15 turns) was the only bound, and it carries no notion of time.
A voice user is gone long before round 14.

Contract under test:
  1. When the deadline is exceeded after a tool round, the loop runs
     exactly ONE final round WITHOUT tools, carrying a directive to
     answer from the gathered evidence — the user always hears a
     grounded answer, never silence and never more tool churn.
  2. Without a deadline (default) behavior is unchanged.
  3. A generous deadline never fires on a fast turn.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from jarvis.brain.iteration_budget import IterationBudget
from jarvis.brain.tool_use_loop import ToolUseLoop
from jarvis.core.protocols import BrainDelta, BrainRequest, ToolResult


class _ListTool:
    name = "wiki-list"
    schema: dict[str, Any] = {}


class _ExecOK:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def execute(self, tool: Any, args: dict[str, Any], **_: Any) -> ToolResult:
        self.calls.append(args)
        return ToolResult(success=True, output="- log.md\n- entities/alex.md")


class _GreedyBrain:
    """Calls a tool on EVERY round that offers tools; answers otherwise.

    Models the runaway agentic loop: it never stops on its own while the
    request still advertises tools.
    """

    def __init__(self) -> None:
        self.requests: list[BrainRequest] = []

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.requests.append(req)
        if req.tools:
            yield BrainDelta(
                tool_call={"id": f"c{len(self.requests)}", "name": "wiki-list", "input": {}}
            )
            yield BrainDelta(finish_reason="tool_use")
            return
        yield BrainDelta(content="Your wiki holds log.md and entities/alex.md.")
        yield BrainDelta(finish_reason="stop")


class _OneShotBrain:
    """One tool round, then a normal answer — the healthy fast turn."""

    def __init__(self) -> None:
        self.requests: list[BrainRequest] = []

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.requests.append(req)
        if len(self.requests) == 1:
            yield BrainDelta(tool_call={"id": "c1", "name": "wiki-list", "input": {}})
            yield BrainDelta(finish_reason="tool_use")
            return
        yield BrainDelta(content="Done quickly.")
        yield BrainDelta(finish_reason="stop")


@pytest.mark.asyncio
async def test_deadline_forces_one_final_toolless_round() -> None:
    brain = _GreedyBrain()
    executor = _ExecOK()
    loop = ToolUseLoop(
        brain,
        {"wiki-list": _ListTool()},
        executor,  # type: ignore[arg-type]
        deadline_s=0.0,  # already expired after the first tool round
    )

    result = await loop.run([], user_utterance="what is in my wiki")

    # Round 1 (tool call) + exactly ONE forced final round — no third round.
    assert len(brain.requests) == 2
    assert executor.calls, "the first tool round must still execute"
    # The forced round must offer NO tools …
    assert not brain.requests[1].tools
    # … and carry the answer-now directive.
    joined = " ".join(
        str(getattr(m, "content", "")) for m in brain.requests[1].messages
    )
    assert "answer" in joined.lower()
    # The user hears a real, grounded answer.
    assert "wiki" in result.text.lower()


@pytest.mark.asyncio
async def test_no_deadline_keeps_budget_behavior() -> None:
    brain = _GreedyBrain()
    loop = ToolUseLoop(
        brain,
        {"wiki-list": _ListTool()},
        _ExecOK(),  # type: ignore[arg-type]
        budget=IterationBudget(max_turns=3),
    )

    result = await loop.run([], user_utterance="what is in my wiki")

    assert result.finish_reason == "budget_exceeded"
    # Every round still offered tools — no forced tool-less round happened.
    assert all(req.tools for req in brain.requests)


@pytest.mark.asyncio
async def test_generous_deadline_never_fires_on_fast_turn() -> None:
    brain = _OneShotBrain()
    loop = ToolUseLoop(
        brain,
        {"wiki-list": _ListTool()},
        _ExecOK(),  # type: ignore[arg-type]
        deadline_s=300.0,
    )

    result = await loop.run([], user_utterance="what is in my wiki")

    assert result.text == "Done quickly."
    assert len(brain.requests) == 2
    # The second (answer) round kept its tools — no forced round.
    assert brain.requests[1].tools
