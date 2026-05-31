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
