"""Steering and phases: a message sent while a turn runs joins the NEXT round
as the person's own words, and the turn says what it is doing."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from jarvis.brain.loop_control import LoopControl
from jarvis.brain.tool_use_loop import ToolUseLoop
from jarvis.core.protocols import BrainDelta, BrainMessage, BrainRequest, ToolResult


class _Tool:
    name = "gmail"
    schema: dict[str, Any] = {}
    description = "mail"


class _Executor:
    async def execute(self, tool: Any, args: dict[str, Any], **_: Any) -> ToolResult:
        return ToolResult(success=True, output="two unread")


class _ToolThenAnswer:
    """One tool round, then an answer — the shape of a real turn."""

    def __init__(self, rounds: int = 1) -> None:
        self.requests: list[BrainRequest] = []
        self._rounds = rounds

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.requests.append(req)
        if len(self.requests) <= self._rounds:
            yield BrainDelta(
                tool_call={"id": f"c{len(self.requests)}", "name": "gmail", "input": {}}
            )
            yield BrainDelta(finish_reason="tool_use")
            return
        yield BrainDelta(content="Two unread mails.")
        yield BrainDelta(finish_reason="stop")


def _loop(brain: Any, control: LoopControl | None) -> ToolUseLoop:
    return ToolUseLoop(brain, {"gmail": _Tool()}, _Executor(), loop_control=control)


def _texts(req: BrainRequest) -> list[str]:
    return [str(m.content) for m in req.messages if m.role == "user"]


async def test_a_steer_message_joins_the_next_round():
    brain = _ToolThenAnswer()
    steer = [["and only the ones from today"], []]
    control = LoopControl(drain_steer=lambda: steer.pop(0) if steer else [])
    out = await _loop(brain, control).run([BrainMessage(role="user", content="check my mail")])
    assert out.text == "Two unread mails."
    second = brain.requests[1]
    assert any("only the ones from today" in t for t in _texts(second))
    # Steering continues the job: the round still carries the tools.
    assert second.tools


async def test_a_steer_after_the_last_round_reopens_the_loop():
    brain = _ToolThenAnswer(rounds=0)  # answers at once
    steer = [[], ["actually, in English please"], []]
    control = LoopControl(drain_steer=lambda: steer.pop(0) if steer else [])
    await _loop(brain, control).run([BrainMessage(role="user", content="hi")])
    assert len(brain.requests) == 2
    assert any("in English please" in t for t in _texts(brain.requests[1]))


async def test_phases_are_reported_once_per_change():
    seen: list[tuple[str, str]] = []

    async def on_phase(phase: str, detail: str) -> None:
        seen.append((phase, detail))

    brain = _ToolThenAnswer()
    await _loop(brain, LoopControl(on_phase=on_phase)).run(
        [BrainMessage(role="user", content="check my mail")]
    )
    assert seen == [("gather", "round 1"), ("act", "gmail"), ("gather", "round 2")]


async def test_a_loop_without_a_control_behaves_exactly_as_before():
    seen: list[Any] = []
    brain = _ToolThenAnswer()
    out = await _loop(brain, None).run([BrainMessage(role="user", content="check my mail")])
    assert out.text == "Two unread mails."
    assert len(brain.requests) == 2 and seen == []


async def test_a_broken_steer_inbox_never_breaks_the_turn():
    def boom() -> list[str]:
        raise RuntimeError("inbox is gone")

    brain = _ToolThenAnswer()
    out = await _loop(brain, LoopControl(drain_steer=boom)).run(
        [BrainMessage(role="user", content="check my mail")]
    )
    assert out.text == "Two unread mails."
