"""Per-response output cap (``[brain].max_tokens``) threading.

Regression context (live 2026-06-01): the voice path built every
``BrainRequest`` without an explicit ``max_tokens``, so the dataclass default
(historically 4096) silently capped every spoken answer. A genuinely long
answer hit the provider's length stop and was read aloud truncated mid-sentence
(no ``finish_reason == "length"`` continuation exists).

These tests pin that the configurable per-response ceiling reaches the actual
``BrainRequest`` on BOTH live construction sites — the tool-use loop
(``tool_use_loop.py``) and the dispatcher simple-mode path (``dispatcher.py``) —
and that the default ceiling is the agreed 8192 (a safety ceiling, NOT a target:
the model still stops naturally via ``finish_reason == "stop"``).
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from jarvis.brain.dispatcher import BrainDispatcher
from jarvis.brain.tool_use_loop import ToolUseLoop
from jarvis.core.config import BrainConfig
from jarvis.core.protocols import BrainDelta, BrainMessage, BrainRequest, ToolResult


class _RecordingBrain:
    """Records every ``BrainRequest`` it is asked to complete, then answers."""

    def __init__(self) -> None:
        self.requests: list[BrainRequest] = []

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.requests.append(req)
        yield BrainDelta(content="Morgen ist Montag.")
        yield BrainDelta(finish_reason="stop")


class _Executor:
    async def execute(self, tool: Any, args: dict[str, Any], **_: Any) -> ToolResult:
        return ToolResult(success=True, output="ok")


def test_brain_config_default_response_cap_is_8192() -> None:
    """The agreed safety ceiling per response is 8192 tokens (raised from 4096)."""
    assert BrainConfig().max_tokens == 8192


@pytest.mark.asyncio
async def test_tool_use_loop_threads_response_max_tokens() -> None:
    brain = _RecordingBrain()
    loop = ToolUseLoop(brain, {}, _Executor(), max_tokens=8192)  # type: ignore[arg-type]

    await loop.run([BrainMessage(role="user", content="Was ist morgen fuer ein Tag?")])

    assert brain.requests, "brain was never called"
    assert brain.requests[0].max_tokens == 8192


@pytest.mark.asyncio
async def test_tool_use_loop_default_cap_is_8192() -> None:
    brain = _RecordingBrain()
    loop = ToolUseLoop(brain, {}, _Executor())  # type: ignore[arg-type]

    await loop.run([BrainMessage(role="user", content="Hallo")])

    assert brain.requests[0].max_tokens == 8192


@pytest.mark.asyncio
async def test_dispatcher_simple_mode_threads_response_max_tokens() -> None:
    brain = _RecordingBrain()
    disp = BrainDispatcher(brain, max_tokens=8192)  # no tools -> simple mode

    await disp.dispatch("Was ist morgen fuer ein Tag?")

    assert brain.requests[0].max_tokens == 8192


@pytest.mark.asyncio
async def test_dispatcher_with_brain_preserves_response_max_tokens() -> None:
    """Provider-switch (``with_brain``) must carry the per-response cap over."""
    brain_a = _RecordingBrain()
    brain_b = _RecordingBrain()
    disp = BrainDispatcher(brain_a, max_tokens=12_000).with_brain(brain_b)

    await disp.dispatch("Hallo")

    assert brain_b.requests[0].max_tokens == 12_000
