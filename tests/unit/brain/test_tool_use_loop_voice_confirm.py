"""ToolUseLoop handling of the voice-confirm deferral sentinel.

When the executor defers a consequential tool on a conversational turn it returns
``ToolResult(error=VOICE_CONFIRM_SENTINEL, ...)``. The loop must then SPEAK a
confirmation question, end the turn (no second brain round), and surface the
pending descriptor so the BrainManager can resume on the next "ja".
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import pytest

from jarvis.brain.tool_use_loop import ToolUseLoop
from jarvis.core.protocols import BrainDelta, BrainRequest, ToolResult
from jarvis.safety.tool_executor import VOICE_CONFIRM_SENTINEL
from jarvis.voice.tool_confirmation import format_tool_confirmation


class _GmailTool:
    name = "gmail"
    schema: dict[str, Any] = {}


class _OneToolBrain:
    """Turn 1: a gmail tool call. (A second turn would mean the loop did NOT
    treat the sentinel as terminal.)"""

    def __init__(self) -> None:
        self.requests: list[BrainRequest] = []

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.requests.append(req)
        if len(self.requests) == 1:
            yield BrainDelta(tool_call={
                "id": "c1", "name": "gmail", "input": {"to": "tom"},
            })
            yield BrainDelta(finish_reason="tool_use")
            return
        yield BrainDelta(content="second round should not happen")
        yield BrainDelta(finish_reason="stop")


class _DeferringExecutor:
    """Stands in for a ToolExecutor that deferred the action (voice-confirm)."""

    def __init__(self) -> None:
        self.trace_id = uuid4()
        self.config_snapshots: list[dict[str, Any]] = []

    async def execute(
        self, tool: Any, args: dict[str, Any], *,
        config_snapshot: dict[str, Any] | None = None, **_: Any,
    ) -> ToolResult:
        self.config_snapshots.append(dict(config_snapshot or {}))
        return ToolResult(
            success=False,
            output={"tool_name": tool.name, "trace_id": str(self.trace_id)},
            error=VOICE_CONFIRM_SENTINEL,
        )


@pytest.mark.asyncio
async def test_sentinel_speaks_question_and_ends_turn() -> None:
    brain = _OneToolBrain()
    executor = _DeferringExecutor()
    spoken: list[str] = []
    loop = ToolUseLoop(brain, {"gmail": _GmailTool()}, executor)  # type: ignore[arg-type]

    result = await loop.run(
        [], user_utterance="schick die Mail an Tom",
        reply_language="de", text_consumer=spoken.append,
    )

    # The turn ended on the sentinel — exactly ONE brain round, no second call.
    assert len(brain.requests) == 1
    assert result.finish_reason == "voice_confirm_pending"
    # The spoken text is the German confirmation question.
    assert result.text == format_tool_confirmation("gmail", language="de")
    assert spoken and spoken[-1] == result.text
    # The pending descriptor is surfaced for the manager's resume.
    assert result.voice_confirm is not None
    assert result.voice_confirm["tool_name"] == "gmail"
    assert result.voice_confirm["trace_id"] == str(executor.trace_id)


@pytest.mark.asyncio
async def test_loop_threads_voice_confirm_into_config_snapshot() -> None:
    brain = _OneToolBrain()
    executor = _DeferringExecutor()
    loop = ToolUseLoop(brain, {"gmail": _GmailTool()}, executor)  # type: ignore[arg-type]

    await loop.run(
        [], user_utterance="schick die Mail an Tom",
        reply_language="de", voice_confirm=True,
    )
    assert executor.config_snapshots
    assert executor.config_snapshots[0].get("voice_confirm") is True


@pytest.mark.asyncio
async def test_voice_confirm_defaults_off_in_config_snapshot() -> None:
    brain = _OneToolBrain()
    executor = _DeferringExecutor()
    loop = ToolUseLoop(brain, {"gmail": _GmailTool()}, executor)  # type: ignore[arg-type]

    await loop.run([], user_utterance="schick die Mail an Tom", reply_language="de")
    # Without the flag the loop must not silently enable deferral.
    assert executor.config_snapshots[0].get("voice_confirm") in (False, None)
