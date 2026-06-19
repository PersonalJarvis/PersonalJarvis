"""The tool-use loop must emit a progress ping on each round + tool boundary.

This is the signal that feeds the speech pipeline's *no-progress* (stall)
timeout (see tests/unit/speech/test_brain_stall_guard.py). A vision/tool turn
streams little or no text, so the pipeline cannot tell "still working" from
"stalled" by watching text chunks alone — the loop has to announce that it is
making progress at the round/tool boundaries that bracket the long silent gaps
(model thinking + tool execution).
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from jarvis.brain.tool_use_loop import ToolUseLoop
from jarvis.core.protocols import BrainDelta, BrainRequest, ToolResult


class _NeutralTool:
    name = "gmail/list_messages"
    schema: dict[str, Any] = {}


class _ExecOK:
    async def execute(self, tool: Any, args: dict[str, Any], **_: Any) -> ToolResult:
        return ToolResult(success=True, output="msg1, msg2")


class _ToolThenAnswerBrain:
    """Round 1: a tool call (no text, like a Gemini vision function_call).
    Round 2: the spoken answer."""

    def __init__(self) -> None:
        self.requests: list[BrainRequest] = []

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.requests.append(req)
        if len(self.requests) == 1:
            yield BrainDelta(
                tool_call={"id": "c1", "name": "gmail/list_messages", "input": {"max": 2}}
            )
            yield BrainDelta(finish_reason="tool_use")
            return
        yield BrainDelta(content="Deine letzten Mails: msg1 und msg2.")
        yield BrainDelta(finish_reason="stop")


@pytest.mark.asyncio
async def test_run_pings_on_progress_across_rounds_and_tools() -> None:
    brain = _ToolThenAnswerBrain()
    loop = ToolUseLoop(
        brain,
        {"gmail/list_messages": _NeutralTool()},
        _ExecOK(),  # type: ignore[arg-type]
    )
    pings = 0

    def _on_progress() -> None:
        nonlocal pings
        pings += 1

    result = await loop.run(
        [],
        user_utterance="zeig mir meine letzten mails",
        on_progress=_on_progress,
    )

    assert "msg1" in result.text
    # Deterministic fake: round 1 (tool_call) → 1 post-aggregate ping; tool
    # executes → 1 tool ping; round 2 (text) → 1 post-aggregate ping = 3. The
    # >=3 floor catches removal of EITHER ping site (post-round OR post-tool),
    # which would silently revert the stall guard to the old broken behavior.
    assert pings >= 3, f"expected >=3 progress pings, got {pings}"


@pytest.mark.asyncio
async def test_run_without_on_progress_is_unchanged() -> None:
    """Default (no callback) must behave exactly as before — additive only."""
    brain = _ToolThenAnswerBrain()
    loop = ToolUseLoop(
        brain,
        {"gmail/list_messages": _NeutralTool()},
        _ExecOK(),  # type: ignore[arg-type]
    )

    result = await loop.run([], user_utterance="zeig mir meine letzten mails")

    assert "msg1" in result.text
