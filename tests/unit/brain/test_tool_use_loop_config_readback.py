"""ToolUseLoop deterministic honest readback for set_config_value (Wave 1.4).

In the voice/chat path config changes apply immediately (no pre-confirm), so the
loop must speak the REAL pipeline outcome and end the turn — never let the brain
phrase a second free-form "done". Mirrors the voice-confirm sentinel handling.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import pytest

# config first → satisfies the writer→config→brain→voice→self_mod import order.
import jarvis.core.config  # noqa: F401, E402  isort:skip
from jarvis.brain.tool_use_loop import ToolUseLoop  # noqa: E402
from jarvis.core.protocols import BrainDelta, BrainRequest, ToolResult  # noqa: E402
from jarvis.core.self_mod import PendingMutation  # noqa: E402


class _ConfigTool:
    name = "set_config_value"
    schema: dict[str, Any] = {}


class _OneToolBrain:
    """Turn 1 calls set_config_value; a turn 2 would mean the loop did NOT
    suppress the second round."""

    def __init__(self) -> None:
        self.requests: list[BrainRequest] = []

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.requests.append(req)
        if len(self.requests) == 1:
            yield BrainDelta(tool_call={
                "id": "c1", "name": "set_config_value",
                "input": {"path": "tts.speed", "new_value": 1.25, "reason": ""},
            })
            yield BrainDelta(finish_reason="tool_use")
            return
        yield BrainDelta(content="second round should not happen")
        yield BrainDelta(finish_reason="stop")


class _Executor:
    def __init__(self, result: ToolResult) -> None:
        self._result = result

    async def execute(self, tool: Any, args: dict[str, Any], **_: Any) -> ToolResult:
        return self._result


def _applied_result() -> ToolResult:
    dump = PendingMutation(
        id=uuid4(), path="tts.speed", old_value=1.0, new_value=1.25,
        needs_confirmation=False, risk_tier="safe", requires_restart=False,
        applied=True, description="TTS speed",
    ).model_dump(mode="json")
    return ToolResult(success=True, output=dump, error=None)


@pytest.mark.asyncio
async def test_applied_speaks_honest_readback_and_ends_turn() -> None:
    brain = _OneToolBrain()
    loop = ToolUseLoop(brain, {"set_config_value": _ConfigTool()}, _Executor(_applied_result()))  # type: ignore[arg-type]
    spoken: list[str] = []

    result = await loop.run(
        [], user_utterance="set the speech speed to one point two five",
        reply_language="en", text_consumer=spoken.append,
    )

    assert len(brain.requests) == 1  # second brain round suppressed
    assert result.finish_reason == "suppress_response"
    assert "1.25" in result.text  # the real value, not a vague "done"
    assert "done" in result.text.lower()
    assert spoken and spoken[-1] == result.text


@pytest.mark.asyncio
async def test_forbidden_never_reads_back_as_done() -> None:
    refused = ToolResult(
        success=False,
        output={"error_kind": "forbidden_path", "path": "security.admin_password_hash"},
        error="forbidden_path: ...",
    )
    brain = _OneToolBrain()
    loop = ToolUseLoop(brain, {"set_config_value": _ConfigTool()}, _Executor(refused))  # type: ignore[arg-type]
    spoken: list[str] = []

    result = await loop.run(
        [], user_utterance="change the admin password hash",
        reply_language="en", text_consumer=spoken.append,
    )

    assert len(brain.requests) == 1  # no free-form second round
    assert result.finish_reason == "suppress_response"
    # The crucial guarantee: a refused change is never spoken as success.
    assert "done" not in result.text.lower()
    assert "security" not in result.text.lower()  # no path leak
