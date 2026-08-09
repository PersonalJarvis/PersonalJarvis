"""The wording probe — "does this provider actually work, here, right now?".

The provider cards call a family "ready" as soon as a key STRING is stored.
Nowhere is that claim weaker than for this pass, because the pass is INVISIBLE
when it fails: it delivers the raw transcript and says nothing. An
out-of-credits account, a model the local server never pulled, and a family
whose answer lands after the latency budget are indistinguishable from a
healthy provider on the card — and all three mean the user's dictation is never
tidied up. On one real install, four of five cards reading "ready" could not
format a sentence.

So the probe asks the provider, and these tests pin what each answer must mean.
No live call is made: the client is injected.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from jarvis.dictation.polish_probe import probe_polish_family


def _cfg(timeout_ms: int = 1200) -> SimpleNamespace:
    return SimpleNamespace(
        dictation=SimpleNamespace(
            polish=True,
            polish_provider="auto",
            polish_model="",
            polish_timeout_ms=timeout_ms,
        )
    )


def _family(family_id: str = "openai") -> Any:
    from jarvis.dictation.polish_client import family_by_id

    return family_by_id(family_id)


def _run(coro):
    return asyncio.run(coro)


class _Answers:
    def __init__(self, text: str = "We can ship it on Tuesday.", delay_s: float = 0.0):
        self._text = text
        self._delay_s = delay_s

    async def complete(self, _system: str, _user: str, **_kw: Any) -> str:
        if self._delay_s:
            await asyncio.sleep(self._delay_s)
        return self._text


class _RecordsAnswer(_Answers):
    def __init__(self) -> None:
        super().__init__()
        self.max_output_tokens = 0

    async def complete(self, _system: str, _user: str, **kw: Any) -> str:
        self.max_output_tokens = int(kw["max_output_tokens"])
        return await super().complete(_system, _user, **kw)


class _Raises:
    def __init__(self, message: str):
        self._message = message

    async def complete(self, _system: str, _user: str, **_kw: Any) -> str:
        raise RuntimeError(self._message)


def test_a_provider_that_answers_in_time_is_ok() -> None:
    res = _run(
        probe_polish_family(
            _family("groq"), _cfg(), make_client=lambda _f, _m: _Answers()
        )
    )

    assert res.status == "ok"
    # Reported under the CARD id, so the UI can attach the verdict to a card.
    assert res.provider == "groq-polish"


def test_probe_reserves_room_for_reasoning_models_final_text() -> None:
    client = _RecordsAnswer()

    _run(
        probe_polish_family(
            _family("groq"), _cfg(), make_client=lambda _f, _m: client
        )
    )

    assert client.max_output_tokens == 256


def test_a_provider_slower_than_the_budget_is_not_reported_healthy() -> None:
    """Reached, authenticated, answered — and still useless HERE.

    The pass has a hard latency ceiling and delivers the RAW transcript when it
    is missed, so a family answering late polishes nothing, every single time.
    Calling that green is the "displayed as working" defect itself.
    """
    res = _run(
        probe_polish_family(
            _family("openrouter"),
            _cfg(timeout_ms=50),
            make_client=lambda _f, _m: _Answers(delay_s=0.25),
        )
    )

    assert res.status != "ok"
    # The sentence must carry the measurement, the budget AND the fix.
    assert "50 ms" in res.detail
    assert "raw text" in res.detail
    assert "faster provider" in res.detail


def test_a_keyless_local_provider_must_still_prove_itself() -> None:
    """Ollama needs no key, and "no key needed" used to read as "ready". A model
    the host never pulled is the ordinary state, and it has to say so."""
    res = _run(
        probe_polish_family(
            _family("ollama"),
            _cfg(),
            make_client=lambda _f, _m: _Raises(
                'HTTP 404: model "llama3.1:8b" not found, try pulling it first'
            ),
        )
    )

    assert res.status == "model_unavailable"


def test_a_depleted_account_is_an_account_state_not_an_integration_bug() -> None:
    """The live finding behind this work: a present, valid, out-of-quota key
    while the card read "ready" and every wording call 429'd."""
    res = _run(
        probe_polish_family(
            _family(),
            _cfg(),
            make_client=lambda _f, _m: _Raises(
                "HTTP 429: You exceeded your current quota, please check your plan"
            ),
        )
    )

    assert res.status == "no_credits"


def test_a_rejected_request_is_reported_rather_than_hidden() -> None:
    """A key that authenticates but whose request the provider refuses (a model
    it has no access to, a bad argument) is a real "this does not work here"."""
    res = _run(
        probe_polish_family(
            _family("gemini"),
            _cfg(),
            make_client=lambda _f, _m: _Raises("400 INVALID_ARGUMENT"),
        )
    )

    assert res.status != "ok"


def test_an_unbuildable_client_is_not_configured() -> None:
    """``build_polish_client`` answers None for "no credential" and for "the SDK
    for this transport is missing" — ordinary states, never a red integration
    error."""
    res = _run(probe_polish_family(_family(), _cfg(), make_client=lambda _f, _m: None))

    assert res.status == "not_configured"


def test_a_silent_answer_is_an_error() -> None:
    res = _run(
        probe_polish_family(
            _family(), _cfg(), make_client=lambda _f, _m: _Answers(text="   ")
        )
    )

    assert res.status == "error"


def test_a_hanging_provider_is_bounded_and_unreachable() -> None:
    """The probe must always produce a verdict — a card whose spinner never
    resolves is its own kind of dishonest."""
    res = _run(
        probe_polish_family(
            _family(),
            # 8s floor / 4x factor: a 1 ms budget still waits the floor, so the
            # bound is asserted through the client raising, not through a sleep
            # the test would have to sit out.
            _cfg(timeout_ms=1200),
            make_client=lambda _f, _m: _Raises("Connection error: getaddrinfo failed"),
        )
    )

    assert res.status == "unreachable"


def test_the_probe_never_raises_on_a_broken_builder() -> None:
    """A probe that throws gives the card nothing to render."""

    def _explode(_family: Any, _model: str) -> Any:
        raise RuntimeError("boom")

    res = _run(probe_polish_family(_family(), _cfg(), make_client=_explode))

    assert res.status in {"error", "unreachable", "not_configured"}
