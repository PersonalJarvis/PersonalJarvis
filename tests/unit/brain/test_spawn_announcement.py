"""Tests for the SpawnAnnouncementComposer — dynamic spawn announcements.

User mandate 2026-06-10: the spoken confirmation when a background worker
is spawned must never be a fixed stock phrase again ("Mach ich, ich
kümmere mich im Hintergrund darum, ..."). The composer prefers a
context-aware phrasing (brain-supplied candidate first, then the flash-LLM
with a dedicated delegation persona) and only then falls back to a small
bilingual no-repeat pool. Guarantees under test:

* never raises, never returns an empty string (AD-OE6 zero silent drops)
* candidate/LLM output is validated: short, right language, no completion
  claims, no internal component names, voice-scrubbed
* de + en both work; the language follows the user's turn
* the fallback pool never repeats the same phrase back-to-back
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from jarvis.brain.ack_brain import CircuitBreaker
from jarvis.brain.ack_brain.config import AckBrainConfig
from jarvis.brain.ack_brain.spawn_announcement import (
    _FALLBACK_ALREADY_RUNNING,
    _FALLBACK_SPAWN,
    SPAWN_PERSONA_DE,
    SPAWN_PERSONA_EN,
    SpawnAnnouncementComposer,
)


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
        self.calls.append({
            "utterance": utterance,
            "language": language,
            "persona_prompt": persona_prompt,
        })
        if self.raises:
            raise RuntimeError("provider boom")
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        return self.reply


def _composer(
    provider: _FakeProvider | None = None,
    *,
    timeout_ms: int = 1500,
) -> SpawnAnnouncementComposer:
    if provider is None:
        return SpawnAnnouncementComposer()
    cfg = AckBrainConfig(timeout_ms=timeout_ms)
    breaker = CircuitBreaker(threshold=3, cooldown_s=60)
    return SpawnAnnouncementComposer(provider=provider, config=cfg, breaker=breaker)


# --------------------------------------------------------------------------- #
# Fallback-only mode (no provider wired — e.g. [ack_brain] disabled)          #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_fallback_only_returns_de_pool_phrase() -> None:
    composer = _composer()
    out = await composer.compose(
        utterance="Schau bitte in meine Mails, ich warte auf eine Rechnung."
    )
    assert out in _FALLBACK_SPAWN["de"]


@pytest.mark.asyncio
async def test_fallback_only_detects_english_turn() -> None:
    composer = _composer()
    out = await composer.compose(
        utterance="Please check my Gmail inbox for new invoices."
    )
    assert out in _FALLBACK_SPAWN["en"]


@pytest.mark.asyncio
async def test_explicit_language_overrides_heuristic() -> None:
    composer = _composer()
    out = await composer.compose(
        utterance="Schau bitte in mein Gmail rein.", language="en"
    )
    assert out in _FALLBACK_SPAWN["en"]


@pytest.mark.asyncio
async def test_fallback_never_repeats_back_to_back() -> None:
    composer = _composer()
    outs = [
        await composer.compose(utterance="Schau in mein Gmail.", language="de")
        for _ in range(12)
    ]
    for a, b in zip(outs, outs[1:], strict=False):
        assert a != b, f"same phrase twice in a row: {a!r}"


@pytest.mark.asyncio
async def test_fallback_varies_across_calls() -> None:
    composer = _composer()
    outs = {
        await composer.compose(utterance="Schau in mein Gmail.", language="de")
        for _ in range(12)
    }
    assert len(outs) >= 3, f"fallback must rotate, saw only {outs!r}"


@pytest.mark.asyncio
async def test_already_running_kind_uses_its_own_pool() -> None:
    composer = _composer()
    out_de = await composer.compose(
        utterance="Schau in mein Gmail.", language="de", kind="already_running"
    )
    out_en = await composer.compose(
        utterance="Check my Gmail please.",
        language="en",
        kind="already_running",
    )
    assert out_de in _FALLBACK_ALREADY_RUNNING["de"]
    assert out_en in _FALLBACK_ALREADY_RUNNING["en"]


def test_pool_phrases_pass_own_validation_and_ban_old_template() -> None:
    """Every curated fallback phrase must survive the composer's own
    validation chain and must not resurrect the 2026-05-26 stock template."""
    composer = _composer()
    for lang, pool in (
        ("de", _FALLBACK_SPAWN["de"]),
        ("en", _FALLBACK_SPAWN["en"]),
        ("de", _FALLBACK_ALREADY_RUNNING["de"]),
        ("en", _FALLBACK_ALREADY_RUNNING["en"]),
    ):
        for phrase in pool:
            assert composer._validate(phrase, lang), (
                f"pool phrase fails own validation ({lang}): {phrase!r}"
            )
            assert len(phrase) <= 120, f"pool phrase too long: {phrase!r}"
            assert "im Hintergrund darum" not in phrase
            assert "komplexe Aufgabe" not in phrase
            assert "vom User beschriebenen Workflow" not in phrase


def test_pools_have_enough_distinct_variants() -> None:
    for pool in (_FALLBACK_SPAWN["de"], _FALLBACK_SPAWN["en"]):
        assert len(pool) >= 6
        assert len(set(pool)) == len(pool)
    for pool in (_FALLBACK_ALREADY_RUNNING["de"], _FALLBACK_ALREADY_RUNNING["en"]):
        assert len(pool) >= 3
        assert len(set(pool)) == len(pool)


# --------------------------------------------------------------------------- #
# LLM path                                                                    #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_provider_text_is_used_when_valid() -> None:
    provider = _FakeProvider(
        reply="Ich schaue gleich in dein Gmail und sage dir Bescheid."
    )
    composer = _composer(provider)
    out = await composer.compose(
        utterance="Schau bitte in mein Gmail rein.", language="de"
    )
    assert "Gmail" in out
    assert out not in _FALLBACK_SPAWN["de"]
    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_provider_gets_de_persona_for_german_turn() -> None:
    """Language unset → composer detects German from the utterance."""
    provider = _FakeProvider(reply="Ich schaue gleich in dein Gmail rein.")
    composer = _composer(provider)
    await composer.compose(
        utterance="Schau bitte nach, ob die Rechnung schon da ist und was drinsteht."
    )
    assert provider.calls[0]["persona_prompt"] == SPAWN_PERSONA_DE


@pytest.mark.asyncio
async def test_provider_gets_en_persona_for_english_turn() -> None:
    provider = _FakeProvider(reply="Checking your Gmail in the background now.")
    composer = _composer(provider)
    await composer.compose(
        utterance="Check my Gmail for new mail.", language="en"
    )
    assert provider.calls[0]["persona_prompt"] == SPAWN_PERSONA_EN


@pytest.mark.asyncio
async def test_provider_content_includes_interpreted_action() -> None:
    provider = _FakeProvider(reply="Ich prüfe gleich deine Gmail-Inbox.")
    composer = _composer(provider)
    await composer.compose(
        utterance="Schau da bitte mal rein.",
        language="de",
        action="die Gmail-Inbox prüft",
        target="auf neue Rechnungen",
    )
    content = provider.calls[0]["utterance"]
    assert "Gmail-Inbox" in content
    assert "auf neue Rechnungen" in content


@pytest.mark.asyncio
async def test_provider_timeout_falls_back() -> None:
    provider = _FakeProvider(
        reply="Ich schaue in dein Gmail.", delay_s=0.5
    )
    composer = _composer(provider, timeout_ms=100)
    out = await composer.compose(
        utterance="Schau in mein Gmail.", language="de"
    )
    assert out in _FALLBACK_SPAWN["de"]


@pytest.mark.asyncio
async def test_provider_error_falls_back() -> None:
    composer = _composer(_FakeProvider(raises=True))
    out = await composer.compose(
        utterance="Schau in mein Gmail.", language="de"
    )
    assert out in _FALLBACK_SPAWN["de"]


@pytest.mark.asyncio
async def test_provider_empty_reply_falls_back() -> None:
    composer = _composer(_FakeProvider(reply="   "))
    out = await composer.compose(
        utterance="Schau in mein Gmail.", language="de"
    )
    assert out in _FALLBACK_SPAWN["de"]


@pytest.mark.asyncio
async def test_overlong_reply_is_trimmed_to_leading_sentences() -> None:
    """Two sentences, the second pushing past the word cap: keep sentence 1."""
    long_tail = " ".join(["und"] * 30)
    provider = _FakeProvider(
        reply=f"Ich schaue kurz in dein Gmail. Danach {long_tail}."
    )
    composer = _composer(provider)
    out = await composer.compose(
        utterance="Schau in mein Gmail.", language="de"
    )
    assert out == "Ich schaue kurz in dein Gmail."


@pytest.mark.asyncio
async def test_monologue_without_fitting_sentence_falls_back() -> None:
    """A single sentence longer than the cap is rambling — reject it."""
    provider = _FakeProvider(
        reply="Ich schaue jetzt sofort gleich heute noch ganz genau und "
        "wirklich ausgesprochen gründlich sowie umfassend und mit aller "
        "gebotenen Sorgfalt in dein gesamtes Gmail-Postfach hinein."
    )
    composer = _composer(provider)
    out = await composer.compose(
        utterance="Schau in mein Gmail.", language="de"
    )
    assert out in _FALLBACK_SPAWN["de"]


@pytest.mark.asyncio
async def test_language_mismatch_falls_back() -> None:
    provider = _FakeProvider(reply="Checking your Gmail right now for you.")
    composer = _composer(provider)
    out = await composer.compose(
        utterance="Schau in mein Gmail.", language="de"
    )
    assert out in _FALLBACK_SPAWN["de"]


@pytest.mark.asyncio
async def test_completion_claim_is_rejected() -> None:
    """The worker has not even started — 'done' claims must never be spoken."""
    provider = _FakeProvider(reply="Die Aufgabe ist erledigt.")
    composer = _composer(provider)
    out = await composer.compose(
        utterance="Schau in mein Gmail.", language="de"
    )
    assert out in _FALLBACK_SPAWN["de"]


@pytest.mark.asyncio
async def test_internal_component_names_are_rejected() -> None:
    provider = _FakeProvider(
        reply="Ich starte einen OpenClaw-Subagent für dein Gmail."
    )
    composer = _composer(provider)
    out = await composer.compose(
        utterance="Schau in mein Gmail.", language="de"
    )
    assert out in _FALLBACK_SPAWN["de"]


@pytest.mark.asyncio
async def test_open_breaker_skips_provider() -> None:
    provider = _FakeProvider(reply="Ich schaue in dein Gmail.")
    cfg = AckBrainConfig(timeout_ms=1500)
    breaker = CircuitBreaker(threshold=1, cooldown_s=60)
    await breaker.record_failure()  # opens immediately at threshold=1
    composer = SpawnAnnouncementComposer(
        provider=provider, config=cfg, breaker=breaker
    )
    out = await composer.compose(
        utterance="Schau in mein Gmail.", language="de"
    )
    assert provider.calls == []
    assert out in _FALLBACK_SPAWN["de"]


# --------------------------------------------------------------------------- #
# Brain-supplied candidate (spoken_ack from the router tool-call)             #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_valid_candidate_short_circuits_llm() -> None:
    provider = _FakeProvider(reply="LLM should not be needed.")
    composer = _composer(provider)
    out = await composer.compose(
        utterance="Schau in mein Gmail.",
        language="de",
        candidate="Ich gehe gleich durch dein Gmail und melde mich.",
    )
    assert "Gmail" in out
    assert provider.calls == []


@pytest.mark.asyncio
async def test_invalid_candidate_falls_through_to_llm() -> None:
    provider = _FakeProvider(reply="Ich schaue gleich in dein Gmail rein.")
    composer = _composer(provider)
    out = await composer.compose(
        utterance="Schau in mein Gmail.",
        language="de",
        candidate="Erledigt.",  # completion claim — must be rejected
    )
    assert out == "Ich schaue gleich in dein Gmail rein."
    assert len(provider.calls) == 1


# --------------------------------------------------------------------------- #
# Hard guarantees                                                             #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_never_raises_and_never_empty_under_pathology() -> None:
    composer = _composer(_FakeProvider(raises=True))
    out = await composer.compose(utterance="", language=None, candidate=None)
    assert isinstance(out, str) and out.strip()
