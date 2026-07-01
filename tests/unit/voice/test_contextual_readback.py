"""Tests for the ReadbackComposer — context-aware spoken status readbacks.

Maintainer mandate: status / outcome / acknowledgement replies must not be
fixed stock phrases read out of a table; they must be phrased for the actual
situation. The composer generates one bounded flash-LLM sentence from the
deterministic facts and falls back to the EXISTING canned line on any failure.
Guarantees under test:

* fallback-only mode (no provider) returns the canned line verbatim
* a healthy provider's sentence is used (and voice-scrubbed)
* timeout / provider error / breaker-open / wrong language → canned fallback
* honesty: a fabricated NUMBER is always rejected; for honesty_bound calls a
  content word absent from the facts is rejected (ADR-0009 rephrase-only)
* an in-progress situation rejects a completion claim
* never raises and never returns an unexpected type (AD-OE6 zero silent drops)
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from jarvis.brain.ack_brain import CircuitBreaker
from jarvis.brain.ack_brain.config import AckBrainConfig
from jarvis.voice.contextual_readback import ReadbackComposer, render_readback

CANNED = "CANNED FALLBACK LINE"


def _canned() -> str:
    return CANNED


class _FakeProvider:
    """Records calls; returns a canned reply, optionally slow or raising."""

    def __init__(
        self,
        reply: str | None = None,
        *,
        delay_s: float = 0.0,
        raises: bool = False,
    ) -> None:
        self.reply = reply
        self.delay_s = delay_s
        self.raises = raises
        self.calls: list[dict[str, Any]] = []

    async def run(
        self, utterance: str, language: str, *, persona_prompt: str
    ) -> str | None:
        self.calls.append(
            {"utterance": utterance, "language": language, "persona": persona_prompt}
        )
        if self.raises:
            raise RuntimeError("provider boom")
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        return self.reply


def _composer(
    provider: _FakeProvider | None = None, *, timeout_ms: int = 1500
) -> ReadbackComposer:
    if provider is None:
        return ReadbackComposer()
    cfg = AckBrainConfig(timeout_ms=timeout_ms)
    breaker = CircuitBreaker(threshold=3, cooldown_s=60)
    return ReadbackComposer(provider=provider, config=cfg, breaker=breaker)


# --------------------------------------------------------------------------- #
# Fallback-only mode (no provider wired)                                       #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_fallback_only_returns_canned() -> None:
    composer = _composer()
    out = await composer.compose(
        instruction="The on-screen task succeeded.",
        language="en",
        canned=_canned,
        facts={"observation": "the browser is open"},
    )
    assert out == CANNED


@pytest.mark.asyncio
async def test_render_readback_none_composer_returns_canned() -> None:
    out = await render_readback(
        None,
        instruction="The on-screen task succeeded.",
        language="de",
        canned=_canned,
    )
    assert out == CANNED


# --------------------------------------------------------------------------- #
# Happy path — generated sentence is used                                      #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_generated_sentence_is_used() -> None:
    provider = _FakeProvider("Sure, your browser is open now.")
    composer = _composer(provider)
    out = await composer.compose(
        instruction="The on-screen task succeeded.",
        language="en",
        canned=_canned,
        facts={"observation": "the browser is open"},
    )
    assert out != CANNED
    assert "browser" in out.lower()
    assert provider.calls, "provider should have been called"


@pytest.mark.asyncio
async def test_spanish_generation_accepted() -> None:
    provider = _FakeProvider("Listo, tu navegador está abierto.")
    composer = _composer(provider)
    out = await composer.compose(
        instruction="The on-screen task succeeded.",
        language="es",
        canned=_canned,
        facts={"observation": "el navegador está abierto"},
    )
    assert out != CANNED
    assert "navegador" in out.lower()


# --------------------------------------------------------------------------- #
# Failure paths fall back to canned                                           #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_timeout_falls_back_to_canned() -> None:
    provider = _FakeProvider("too slow", delay_s=0.5)
    composer = _composer(provider)
    out = await composer.compose(
        instruction="The on-screen task succeeded.",
        language="en",
        canned=_canned,
        latency_budget_ms=20,  # per-call hard deadline beats the 0.5 s provider
    )
    assert out == CANNED


@pytest.mark.asyncio
async def test_provider_error_falls_back_to_canned() -> None:
    provider = _FakeProvider(raises=True)
    composer = _composer(provider)
    out = await composer.compose(
        instruction="The on-screen task succeeded.",
        language="en",
        canned=_canned,
    )
    assert out == CANNED


@pytest.mark.asyncio
async def test_empty_output_falls_back_to_canned() -> None:
    provider = _FakeProvider("   ")
    composer = _composer(provider)
    out = await composer.compose(
        instruction="The on-screen task succeeded.",
        language="en",
        canned=_canned,
    )
    assert out == CANNED


@pytest.mark.asyncio
async def test_wrong_language_falls_back_to_canned() -> None:
    # German output requested for an English turn -> rejected -> canned.
    provider = _FakeProvider("Das hat auf dem Bildschirm geklappt und ist fertig.")  # i18n-allow
    composer = _composer(provider)
    out = await composer.compose(
        instruction="The on-screen task succeeded.",
        language="en",
        canned=_canned,
    )
    assert out == CANNED


# --------------------------------------------------------------------------- #
# Honesty guards                                                               #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_fabricated_number_rejected() -> None:
    # facts carry no number; the model invents "5 tabs" -> rejected.
    provider = _FakeProvider("I opened 5 tabs for you.")
    composer = _composer(provider)
    out = await composer.compose(
        instruction="The on-screen task succeeded.",
        language="en",
        canned=_canned,
        facts={"observation": "the browser is open"},
    )
    assert out == CANNED


@pytest.mark.asyncio
async def test_number_from_facts_allowed() -> None:
    provider = _FakeProvider("Your browser is open with 3 tabs.")
    composer = _composer(provider)
    out = await composer.compose(
        instruction="The on-screen task succeeded.",
        language="en",
        canned=_canned,
        facts={"observation": "the browser is open with 3 tabs"},
    )
    assert out != CANNED
    assert "3" in out


@pytest.mark.asyncio
async def test_honesty_bound_rejects_unsupported_content_word() -> None:
    provider = _FakeProvider("The calculator is showing the result.")
    composer = _composer(provider)
    out = await composer.compose(
        instruction="The on-screen task succeeded.",
        language="en",
        canned=_canned,
        facts={"observation": "the browser is open with your inbox"},
        honesty_bound=True,
    )
    assert out == CANNED


@pytest.mark.asyncio
async def test_honesty_bound_accepts_rephrase_of_facts() -> None:
    provider = _FakeProvider("Your browser is open with the inbox.")
    composer = _composer(provider)
    out = await composer.compose(
        instruction="The on-screen task succeeded.",
        language="en",
        canned=_canned,
        facts={"observation": "the browser is open with your inbox"},
        honesty_bound=True,
    )
    assert out != CANNED
    assert "inbox" in out.lower()


# --------------------------------------------------------------------------- #
# In-progress + forbidden vocab                                               #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_in_progress_rejects_completion_claim() -> None:
    provider = _FakeProvider("That is done on screen.")
    composer = _composer(provider)
    out = await composer.compose(
        instruction="About to do the task on screen in the background.",
        language="en",
        canned=_canned,
        in_progress=True,
    )
    assert out == CANNED


@pytest.mark.asyncio
async def test_forbidden_vocab_rejected() -> None:
    provider = _FakeProvider("The harness returned exit 5 from the subprocess.")
    composer = _composer(provider)
    out = await composer.compose(
        instruction="The on-screen task failed.",
        language="en",
        canned=_canned,
    )
    assert out == CANNED


# --------------------------------------------------------------------------- #
# Robustness — never raises, even when the canned fallback is broken           #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_compose_never_raises_when_canned_raises() -> None:
    def _boom() -> str:
        raise RuntimeError("canned boom")

    composer = _composer()  # no provider -> must use canned, which raises
    out = await composer.compose(
        instruction="The on-screen task succeeded.",
        language="en",
        canned=_boom,
    )
    assert out == ""  # graceful empty, no exception


@pytest.mark.asyncio
async def test_has_llm_flag() -> None:
    assert _composer().has_llm is False
    assert _composer(_FakeProvider("hi there friend")).has_llm is True
