"""The probe's construction step runs off the event loop (BUG-189).

Instantiating a provider is, on a cold process, the first import of its SDK —
pydantic model generation for a few hundred types, plus the registry's
entry-point discovery that imports every brain plugin. Done ON the loop, that
ran for 15 s while the agent chat's health sweep probed every provider at boot
(2026-08-27), and every WebSocket frame, route and brain turn stood still
behind it. The checker hands the registry call to a worker thread; these pin
that the loop thread is never the one constructing, and that a constructor
that raises is still an honest, non-raising result.
"""

from __future__ import annotations

import threading
from collections.abc import AsyncIterator
from typing import Any

import pytest

from jarvis.brain.healthcheck import BrainHealthChecker
from jarvis.core.protocols import BrainDelta, BrainRequest


class _InstantBrain:
    name = "instant-brain"
    context_window = 8192
    supports_tools = False
    supports_vision = False

    def __init__(self, *, model: str) -> None:
        self.model = model

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        yield BrainDelta(content="hi", finish_reason="stop")

    def estimate_cost(self, req: BrainRequest) -> float:  # pragma: no cover
        return 0.0


class _ThreadRecordingRegistry:
    """Same ``instantiate`` signature as production; remembers who called."""

    def __init__(self) -> None:
        self.threads: list[int] = []

    def instantiate(self, name: str, **kwargs: Any) -> Any:
        self.threads.append(threading.get_ident())
        return _InstantBrain(**kwargs)


class _RefusingRegistry:
    def instantiate(self, name: str, **kwargs: Any) -> Any:
        raise RuntimeError("No Anthropic API key found.")


@pytest.mark.asyncio
async def test_construction_happens_off_the_loop_thread() -> None:
    registry = _ThreadRecordingRegistry()
    checker = BrainHealthChecker(registry)  # type: ignore[arg-type]

    result = await checker.probe("some-provider", "some-model")

    assert result.ok is True
    assert registry.threads, "the registry was never asked"
    assert all(ident != threading.get_ident() for ident in registry.threads), (
        "the provider was constructed on the event loop thread"
    )


@pytest.mark.asyncio
async def test_a_constructor_that_raises_is_a_clean_failure_not_an_exception() -> None:
    checker = BrainHealthChecker(_RefusingRegistry())  # type: ignore[arg-type]

    result = await checker.probe("claude-api", "claude-haiku-4-5-20251001")

    assert result.ok is False
    assert result.error is not None
    assert "No Anthropic API key" in result.error
