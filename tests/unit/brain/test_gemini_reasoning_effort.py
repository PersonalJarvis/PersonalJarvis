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

    def __init__(
        self,
        *,
        reject_thinking: bool = False,
        reject_message: str = (
            "400 INVALID_ARGUMENT. Budget 0 is invalid. This model only "
            "works in thinking mode."
        ),
        reject_thinking_generic: bool = False,
        reject_everything_generic: bool = False,
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self.reject_thinking = reject_thinking
        self.reject_message = reject_message
        self.reject_thinking_generic = reject_thinking_generic
        self.reject_everything_generic = reject_everything_generic
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
            raise RuntimeError(self.reject_message)
        if (
            self.reject_everything_generic
            or (
                self.reject_thinking_generic
                and config.get("thinking_config") is not None
            )
        ):
            # The parameterless rejection shape gemini-3.6-flash produced
            # live 2026-07-21 — no field name, no "thinking" token.
            raise RuntimeError(
                "400 INVALID_ARGUMENT. {'error': {'code': 400, 'message': "
                "'Request contains an invalid argument.', 'status': "
                "'INVALID_ARGUMENT'}}"
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
async def test_parameterless_400_recovers_and_is_remembered() -> None:
    """Live 2026-07-21: ``gemini-3.6-flash`` rejects ``thinking_budget=0``
    with the bare "Request contains an invalid argument." — no "thinking" in
    the message. The recovery must (a) retry once without ``thinking_config``
    and yield the answer, and (b) once that retry is accepted, skip the field
    on every later turn so each delegated realtime turn doesn't pay a doomed
    extra round trip."""
    provider = GeminiBrain(model="gemini-3.6-flash")
    fake = _FakeGeminiClient(reject_thinking_generic=True)
    provider._client = fake  # type: ignore[assignment]

    deltas = await _drain(provider.complete(_request("none")))

    assert len(fake.calls) == 2
    assert fake.calls[0].get("thinking_config") is not None
    assert "thinking_config" not in fake.calls[1]
    assert any(d.content for d in deltas), "recovered stream must yield text"

    # Second turn: the blame is proven — no thinking_config, no extra 400.
    await _drain(provider.complete(_request("none")))
    assert len(fake.calls) == 3
    assert "thinking_config" not in fake.calls[2]


@pytest.mark.asyncio
async def test_unrelated_400_still_propagates_and_forgets_nothing() -> None:
    """A parameterless 400 whose retry ALSO fails is not the thinking
    config's fault: the error must propagate and the instance must NOT stop
    sending ``thinking_config`` on later turns."""
    provider = GeminiBrain(model="gemini-3.6-flash")
    fake = _FakeGeminiClient(reject_everything_generic=True)
    provider._client = fake  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="invalid argument"):
        await _drain(provider.complete(_request("none")))

    # One original attempt + one blame-probe retry, then the error surfaced.
    assert len(fake.calls) == 2

    fake.reject_everything_generic = False
    await _drain(provider.complete(_request("none")))
    assert fake.calls[2].get("thinking_config") is not None, (
        "an unproven blame must not permanently disable thinking_config"
    )


@pytest.mark.asyncio
async def test_generic_400_with_live_cache_clears_cache_and_blames_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A parameterless 400 while a ``cached_content`` reference is on the
    wire could be a differently-worded dead-cache rejection: the recovery
    must clear the cache reference alongside ``thinking_config`` (else the
    poisoned name survives forever — BUG-019's symptom) and must NOT blame
    the thinking budget, because with two variables changed the accepted
    retry proves nothing."""
    monkeypatch.setenv("JARVIS_GEMINI_CONTEXT_CACHE", "1")

    system_text = "X" * (4096 * 4 + 100)  # above the _MIN_CACHE_TOKENS floor
    provider = GeminiBrain(model="gemini-3.6-flash")
    fake = _FakeGeminiClient()
    provider._client = fake  # type: ignore[assignment]
    provider._cached_content_name = "cachedContents/dead-id"
    provider._cache_signature = (str(hash(system_text)), "")

    async def _reject_cache_or_thinking(
        *, model: str, contents: list[Any], config: dict[str, Any]
    ) -> Any:
        fake.calls.append(dict(config))
        if config.get("cached_content") or config.get("thinking_config"):
            raise RuntimeError(
                "400 INVALID_ARGUMENT. {'error': {'code': 400, 'message': "
                "'Request contains an invalid argument.', 'status': "
                "'INVALID_ARGUMENT'}}"
            )

        async def _stream() -> AsyncIterator[Any]:
            yield SimpleNamespace(text="OK", candidates=[], usage_metadata=None)

        return _stream()

    fake.generate_content_stream = _reject_cache_or_thinking  # type: ignore[method-assign]

    req = BrainRequest(
        messages=(BrainMessage(role="user", content="ping"),),
        max_tokens=320,
        system=system_text,
        reasoning_effort="none",  # type: ignore[arg-type]
    )
    deltas = await _drain(provider.complete(req))

    assert len(fake.calls) == 2
    assert fake.calls[0].get("cached_content") == "cachedContents/dead-id"
    assert fake.calls[0].get("thinking_config") is not None
    assert "cached_content" not in fake.calls[1]
    assert "thinking_config" not in fake.calls[1]
    assert any(d.content for d in deltas), "recovered stream must yield text"
    assert provider._cached_content_name is None, (
        "the possibly-dead cache reference must be invalidated"
    )
    assert provider._rejected_thinking_budgets == set(), (
        "a two-variable retry must not blame the thinking budget"
    )
