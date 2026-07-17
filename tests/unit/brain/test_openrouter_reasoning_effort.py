"""OpenRouter mapping of the per-request ``reasoning_effort="none"`` hint.

Delegated realtime voice turns opt out of internal model reasoning (see
``test_tool_use_loop_reasoning_effort.py``). When the Tool-Model chain
crosses to the OpenRouter gateway, that hint must become OpenRouter's
unified ``reasoning`` parameter — otherwise a gatewayed thinking-by-default
model (e.g. google/gemini-*-flash) keeps burning seconds of thought per
tool-loop round and the hint silently does nothing.

Contract under test:
  1. ``reasoning_effort="none"`` → ``extra_body={"reasoning": {"enabled":
     False}}`` on the wire call.
  2. Default requests add NO reasoning parameter (unchanged behavior).
  3. Fail open: if the upstream rejects the reasoning parameter itself, the
     call retries once WITHOUT it and still streams — a latency hint must
     never brick a turn. Unrelated errors propagate unchanged.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

import jarvis.plugins.brain.openrouter as openrouter_mod
from jarvis.core.protocols import BrainDelta, BrainMessage, BrainRequest
from jarvis.plugins.brain.openrouter import OpenRouterBrain


def _req(reasoning_effort: Any = None) -> BrainRequest:
    return BrainRequest(
        messages=(BrainMessage(role="user", content="hi"),),
        reasoning_effort=reasoning_effort,
    )


def _brain(monkeypatch: pytest.MonkeyPatch) -> OpenRouterBrain:
    brain = OpenRouterBrain("x/some-model")
    monkeypatch.setattr(brain, "_ensure_client", lambda: object())
    return brain


@pytest.mark.asyncio
async def test_none_hint_becomes_reasoning_disabled_extra_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[dict[str, Any] | None] = []

    async def fake_stream_complete(
        client: Any, model: str, req: BrainRequest, *, extra_body: Any = None, **_: Any
    ) -> AsyncIterator[BrainDelta]:
        seen.append(extra_body)
        yield BrainDelta(content="ok")
        yield BrainDelta(finish_reason="stop")

    monkeypatch.setattr(openrouter_mod, "stream_complete", fake_stream_complete)
    brain = _brain(monkeypatch)

    deltas = [d async for d in brain.complete(_req(reasoning_effort="none"))]

    assert seen == [{"reasoning": {"enabled": False}}]
    assert any(d.content == "ok" for d in deltas)


@pytest.mark.asyncio
async def test_default_request_sends_no_reasoning_parameter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[dict[str, Any] | None] = []

    async def fake_stream_complete(
        client: Any, model: str, req: BrainRequest, *, extra_body: Any = None, **_: Any
    ) -> AsyncIterator[BrainDelta]:
        seen.append(extra_body)
        yield BrainDelta(content="ok")

    monkeypatch.setattr(openrouter_mod, "stream_complete", fake_stream_complete)
    brain = _brain(monkeypatch)

    [d async for d in brain.complete(_req())]

    assert seen == [None]


@pytest.mark.asyncio
async def test_rejected_reasoning_parameter_retries_without_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any] | None] = []

    async def fake_stream_complete(
        client: Any, model: str, req: BrainRequest, *, extra_body: Any = None, **_: Any
    ) -> AsyncIterator[BrainDelta]:
        calls.append(extra_body)
        if extra_body is not None:
            raise RuntimeError("400: unknown parameter 'reasoning'")
        yield BrainDelta(content="fallback ok")
        yield BrainDelta(finish_reason="stop")

    monkeypatch.setattr(openrouter_mod, "stream_complete", fake_stream_complete)
    brain = _brain(monkeypatch)

    deltas = [d async for d in brain.complete(_req(reasoning_effort="none"))]

    assert calls == [{"reasoning": {"enabled": False}}, None]
    assert any(d.content == "fallback ok" for d in deltas)


@pytest.mark.asyncio
async def test_unrelated_error_propagates_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_stream_complete(
        client: Any, model: str, req: BrainRequest, *, extra_body: Any = None, **_: Any
    ) -> AsyncIterator[BrainDelta]:
        raise RuntimeError("401: invalid api key")
        yield BrainDelta(content="unreachable")  # pragma: no cover

    monkeypatch.setattr(openrouter_mod, "stream_complete", fake_stream_complete)
    brain = _brain(monkeypatch)

    with pytest.raises(RuntimeError, match="invalid api key"):
        [d async for d in brain.complete(_req(reasoning_effort="none"))]
