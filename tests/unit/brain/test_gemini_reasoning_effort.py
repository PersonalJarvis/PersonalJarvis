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
* That rejection is REMEMBERED per model, so the recovery runs once instead of
  on every request (live forensic 2026-07-25: 154 rejections across four
  desktop logs, ~8 s of added turn latency on 28 % of turns).
* But it is remembered only on PROOF — the retry without the field has to
  succeed. A generic INVALID_ARGUMENT from an unrelated cause must never
  permanently strip the thinking budget from a model that supports it.

Uses the same fake-client shape as ``test_gemini_stale_cache_bug019.py``;
``google.genai`` must be importable for ``ThinkingConfig`` to be attached,
so these tests skip cleanly on environments without the SDK.
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.core.protocols import BrainMessage, BrainRequest
from jarvis.plugins.brain import gemini as gemini_mod
from jarvis.plugins.brain.gemini import GeminiBrain

pytest.importorskip("google.genai")


@pytest.fixture(autouse=True)
def _clean_thinking_capability_cache() -> Iterator[None]:
    """The proven-rejection set is process-wide; keep it out of other tests.

    Without this the recovery tests below would leak a cached model into any
    later test using the same id, making the suite order-dependent.
    """
    gemini_mod._THINKING_CONFIG_REJECTED.clear()
    yield
    gemini_mod._THINKING_CONFIG_REJECTED.clear()


class _FakeGeminiClient:
    """Records every ``config`` passed to ``generate_content_stream``."""

    def __init__(
        self,
        *,
        reject_thinking: bool = False,
        reject_always: bool = False,
        reject_message: str = (
            "400 INVALID_ARGUMENT. Budget 0 is invalid. This model only "
            "works in thinking mode."
        ),
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self.reject_thinking = reject_thinking
        # Rejects regardless of thinking_config — models an INVALID_ARGUMENT
        # whose real cause is something else entirely.
        self.reject_always = reject_always
        self.reject_message = reject_message
        self.aio = SimpleNamespace(models=self)

    async def generate_content_stream(
        self,
        *,
        model: str,
        contents: list[Any],
        config: dict[str, Any],
    ) -> AsyncIterator[Any]:
        self.calls.append(dict(config))
        if self.reject_always:
            raise RuntimeError(self.reject_message)
        if self.reject_thinking and config.get("thinking_config") is not None:
            raise RuntimeError(self.reject_message)

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


@pytest.mark.asyncio
async def test_generic_invalid_argument_recovers_without_the_field() -> None:
    """A NEWER thinking-mandatory model (live 2026-07-23: gemini-3.6-flash)
    rejects ``thinking_budget=0`` with only the GENERIC "Request contains an
    invalid argument." 400 — no "thinking"/"budget" token. It must still be
    recognised as a thinking-config rejection and recover via ONE retry
    without the field, or the whole vision chain falls through to a blind
    last-resort brain and the user hears "couldn't get a valid screen-control
    response"."""
    provider = GeminiBrain(model="gemini-3.6-flash")
    fake = _FakeGeminiClient(
        reject_thinking=True,
        reject_message=(
            '400 Bad Request. {"error": {"code": 400, "message": "Request '
            'contains an invalid argument.", "status": "INVALID_ARGUMENT"}}'
        ),
    )
    provider._client = fake  # type: ignore[assignment]

    deltas = await _drain(provider.complete(_request("none")))

    assert len(fake.calls) == 2, (
        "the generic INVALID_ARGUMENT 400 must trigger exactly one retry "
        "without thinking_config"
    )
    assert fake.calls[0].get("thinking_config") is not None
    assert "thinking_config" not in fake.calls[1]
    assert any(d.content for d in deltas), "recovered stream must yield text"


@pytest.mark.asyncio
async def test_a_proven_rejection_is_remembered_for_later_calls() -> None:
    """The recovery must run ONCE per model, not once per request.

    Live forensic 2026-07-25: the recovery was correct but had no memory, so
    every single request re-probed the rejected field — 154 rejections across
    four desktop logs, adding ~8 s to 48 of 169 conversation turns (28 %). A
    correct recovery path taken on every request is not a fallback any more, it
    is the main path.
    """
    provider = GeminiBrain(model="gemini-3.6-flash")
    fake = _FakeGeminiClient(reject_thinking=True)
    provider._client = fake  # type: ignore[assignment]

    await _drain(provider.complete(_request("none")))
    assert len(fake.calls) == 2, "first call: probe, then recover"

    # Second request: the field is known-bad, so it must not go on the wire
    # again — one call, no rejection, no retry.
    await _drain(provider.complete(_request("none")))

    assert len(fake.calls) == 3, (
        "the second request must cost exactly ONE call — re-probing a proven "
        "rejection is the 8-second-per-turn regression this cache removes"
    )
    assert "thinking_config" not in fake.calls[2]


@pytest.mark.asyncio
async def test_an_unrelated_invalid_argument_is_never_remembered() -> None:
    """Proof is required before caching, because the error cannot name a cause.

    The rejection arrives as a bare "Request contains an invalid argument."
    with no "thinking" token, so it is indistinguishable from an unrelated bad
    argument (a malformed tool schema, an oversized context). If the failure is
    NOT the thinking config, dropping the field does not help — and the model
    must keep its thinking budget on the next request instead of being
    permanently downgraded with no diagnosable trace.
    """
    provider = GeminiBrain(model="gemini-3.6-flash")
    fake = _FakeGeminiClient(
        reject_always=True,
        reject_message=(
            '400 Bad Request. {"error": {"code": 400, "message": "Request '
            'contains an invalid argument.", "status": "INVALID_ARGUMENT"}}'
        ),
    )
    provider._client = fake  # type: ignore[assignment]

    with pytest.raises(Exception):  # noqa: B017 — SDK error class varies
        await _drain(provider.complete(_request("none")))

    assert not gemini_mod._THINKING_CONFIG_REJECTED, (
        "a failure that persists WITHOUT thinking_config proves the field was "
        "innocent; caching it would strip thinking from a capable model"
    )

    # Next request must still probe with the field attached.
    fake.reject_always = False
    fake.reject_thinking = False
    await _drain(provider.complete(_request("none")))

    assert fake.calls[-1].get("thinking_config") is not None, (
        "the thinking budget must survive an unrelated INVALID_ARGUMENT"
    )
