from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from jarvis.brain.tool_use_loop import ToolUseLoop
from jarvis.core.protocols import BrainDelta, BrainRequest, ToolResult


class _Tool:
    name = "dispatch_to_harness"
    schema: dict[str, Any] = {}


class _Executor:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, dict[str, Any]]] = []

    async def execute(
        self,
        tool: Any,
        args: dict[str, Any],
        **_: Any,
    ) -> ToolResult:
        self.calls.append((tool, args))
        return ToolResult(success=True, output="executed")


class _Brain:
    def __init__(self) -> None:
        self.requests: list[BrainRequest] = []

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.requests.append(req)
        if len(self.requests) == 1:
            yield BrainDelta(
                tool_call={
                    "id": "call_1",
                    "name": "dispatch_to_harness",
                    "input": {
                        "harness": "computer-use",
                        "prompt": "Wie kann ich bei Windows reinzoomen?",
                    },
                }
            )
            yield BrainDelta(finish_reason="tool_use")
            return

        yield BrainDelta(content="Mit Windows-Taste plus Pluszeichen zoomst du rein.")
        yield BrainDelta(finish_reason="stop")


@pytest.mark.asyncio
async def test_how_to_question_blocks_side_effect_tool() -> None:
    brain = _Brain()
    executor = _Executor()
    loop = ToolUseLoop(
        brain,
        {"dispatch_to_harness": _Tool()},
        executor,  # type: ignore[arg-type]
    )

    result = await loop.run(
        [],
        user_utterance="Wie kann ich bei Windows reinzoomen?",
    )

    assert executor.calls == []
    assert "Windows-Taste" in result.text
    assert len(brain.requests) == 2
    tool_message = brain.requests[1].messages[-1].content
    assert "How-to" in str(tool_message)


class _GmailTool:
    name = "gmail/list_messages"
    schema: dict[str, Any] = {}


class _ExecOK:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, dict[str, Any]]] = []

    async def execute(self, tool: Any, args: dict[str, Any], **_: Any) -> ToolResult:
        self.calls.append((tool, args))
        return ToolResult(success=True, output="msg1, msg2, msg3, msg4, msg5")


class _BigContextBrain:
    """First turn: 'Einen Moment.' + a tool call, reporting a HUGE input-token
    count (a long voice session). Second turn: the actual answer."""

    def __init__(self) -> None:
        self.requests: list[BrainRequest] = []

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.requests.append(req)
        if len(self.requests) == 1:
            yield BrainDelta(content="Einen Moment.")
            yield BrainDelta(tool_call={
                "id": "c1", "name": "gmail/list_messages", "input": {"max": 5},
            })
            # The re-sent prompt (system + tools + whole history) is ~60k tokens.
            yield BrainDelta(usage={"input_tokens": 60_000, "output_tokens": 40})
            yield BrainDelta(finish_reason="tool_use")
            return
        yield BrainDelta(content="Deine letzten 5 Mails: msg1 bis msg5.")
        yield BrainDelta(usage={"input_tokens": 60_000, "output_tokens": 30})
        yield BrainDelta(finish_reason="stop")


@pytest.mark.asyncio
async def test_tool_executes_despite_huge_per_turn_input_tokens() -> None:
    """Regression (live bug 2026-06-01): in a long conversation a single turn's
    re-sent prompt is ~50-60k tokens. The cumulative token budget must NOT be
    exhausted by that re-sent input — otherwise the loop aborts after the model
    asks for a tool but BEFORE executing it, and the user hears only the bare
    'Einen Moment.' ack (an AD-OE6 silent drop). The tool must run AND the loop
    must do a second turn to report the result."""
    brain = _BigContextBrain()
    executor = _ExecOK()
    loop = ToolUseLoop(
        brain,
        {"gmail/list_messages": _GmailTool()},
        executor,  # type: ignore[arg-type]
    )

    result = await loop.run(
        [],
        user_utterance="Kannst du mal die letzten 5 E-Mails sagen?",
    )

    assert executor.calls, "the Gmail tool must execute despite the large prompt"
    assert "msg1 bis msg5" in result.text or "5 Mails" in result.text
    assert len(brain.requests) == 2, "the loop must do a second turn to report"
