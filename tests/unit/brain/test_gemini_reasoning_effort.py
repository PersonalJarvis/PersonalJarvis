"""GeminiBrain mapping of ``BrainRequest.reasoning_effort`` to thinking config.

Live forensic 2026-07-16: the fast vision model (``gemini-3.5-flash``,
a preview alias) turned thinking-by-default server-side. Computer-Use calls
cap ``max_output_tokens`` at 320, and Gemini counts thoughts against that
cap — reproduced 1:1 against the live API: thoughts=304, candidate=12,
finish=MAX_TOKENS, visible reply ``{"action": "open_app", "name": "`` →
every CU step failed "unterminated JSON" and the mission aborted.

Contract pinned here:

* ``reasoning_effort="none"`` on the request → ``thinking_config`` with
  ``thinking_budget=0`` goes on the wire (thinking disabled for this call).
* An explicit constructor ``thinking_budget`` always wins over the hint.
* No hint, no constructor budget → no ``thinking_config`` (SDK default).
* A model that REJECTS budget=0 (thinking-mandatory Pro class answers 400
  "Budget 0 is invalid. This model only works in thinking mode.") is
  recovered by ONE retry without the field — capability probe, not a
  model-name pin (AP-21).

Uses the same fake-client shape as ``test_gemini_stale_cache_bug019.py``;
``google.genai`` must be importable for ``ThinkingConfig`` to be attached,
so these tests skip cleanly on environments without the SDK.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.core.protocols import BrainMessage, BrainRequest
from jarvis.plugins.brain.gemini import GeminiBrain

pytest.importorskip("google.genai")


class _FakeGeminiClient:
    """Records every ``config`` passed to ``generate_content_stream``."""

    def __init__(self, *, reject_thinking: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self.reject_thinking = reject_thinking
        self.aio = SimpleNamespace(models=self)

    async def generate_content_stream(
        self,
        *,
        model: str,
        contents: list[Any],
        config: dict[str, Any],
    ) -> AsyncIterator[Any]:
        self.calls.append(dict(config))
        if self.reject_thinking and config.get("thinking_config") is not None:
            raise RuntimeError(
                "400 INVALID_ARGUMENT. Budget 0 is invalid. This model only "
                "works in thinking mode."
            )

        async def _stream() -> AsyncIterator[Any]:
            yield SimpleNamespace(
                text='{"action": "done"}',
                candidates=[],
                usage_metadata=None,
            )

        return _stream()


async def _drain(stream: AsyncIterator[Any]) -> list[Any]:
    out: list[Any] = []
    async for chunk in stream:
        out.append(chunk)
    return out


def _request(reasoning_effort: str | None) -> BrainRequest:
    return BrainRequest(
        messages=(BrainMessage(role="user", content="ping"),),
        max_tokens=320,
        reasoning_effort=reasoning_effort,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_reasoning_effort_none_disables_thinking() -> None:
    provider = GeminiBrain(model="gemini-3.5-flash")
    fake = _FakeGeminiClient()
    provider._client = fake  # type: ignore[assignment]

    await _drain(provider.complete(_request("none")))

    assert fake.calls, "no call captured"
    tc = fake.calls[0].get("thinking_config")
    assert tc is not None, (
        "reasoning_effort='none' must attach a thinking_config — without it "
        "a thinking-by-default model eats the 320-token output budget"
    )
    assert getattr(tc, "thinking_budget", None) == 0


@pytest.mark.asyncio
async def test_explicit_constructor_budget_wins_over_hint() -> None:
    provider = GeminiBrain(model="gemini-3.5-flash", thinking_budget=128)
    fake = _FakeGeminiClient()
    provider._client = fake  # type: ignore[assignment]

    await _drain(provider.complete(_request("none")))

    tc = fake.calls[0].get("thinking_config")
    assert getattr(tc, "thinking_budget", None) == 128


@pytest.mark.asyncio
async def test_no_hint_keeps_sdk_default() -> None:
    provider = GeminiBrain(model="gemini-3.5-flash")
    fake = _FakeGeminiClient()
    provider._client = fake  # type: ignore[assignment]

    await _drain(provider.complete(_request(None)))

    assert "thinking_config" not in fake.calls[0]


@pytest.mark.asyncio
async def test_thinking_mandatory_model_recovers_without_the_field() -> None:
    """A 400 "only works in thinking mode" rejection retries ONCE without
    ``thinking_config`` and the stream succeeds — the hint can never brick a
    provider whose model insists on thinking."""
    provider = GeminiBrain(model="gemini-3.5-pro")
    fake = _FakeGeminiClient(reject_thinking=True)
    provider._client = fake  # type: ignore[assignment]

    deltas = await _drain(provider.complete(_request("none")))

    assert len(fake.calls) == 2
    assert fake.calls[0].get("thinking_config") is not None
    assert "thinking_config" not in fake.calls[1]
    assert any(d.content for d in deltas), "recovered stream must yield text"
