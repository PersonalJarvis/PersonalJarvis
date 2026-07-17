"""Tests for the SpawnAnnouncementComposer — dynamic spawn announcements.

User mandate 2026-06-10: the spoken confirmation when a background worker
is spawned must never be a fixed stock phrase again ("Mach ich, ich
kümmere mich im Hintergrund darum, ..."). The composer prefers a
context-aware phrasing (brain-supplied candidate first, then the flash-LLM
with a dedicated delegation persona) and only then falls back to a small
bilingual no-repeat pool. Guarantees under test:

* never raises, never returns an empty string (AD-OE6 zero silent drops)
* candidate/LLM output is validated: short, right language, the public
  agent brand (wake-word-derived assistant name + "-Agent") is explicit,
  no completion claims, no internal component names, voice-scrubbed
* de + en both work; the language follows the user's turn
* the fallback pool never repeats the same phrase back-to-back

The tests run with the arbitrary brand "Nova-Agent" wired through
``brand_provider`` — the brand must follow ANY configured wake word,
never one blessed product name.
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
    STILL_RUNNING_PHRASES,
    SpawnAnnouncementComposer,
    get_spawn_persona,
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


# The arbitrary test brand — deliberately NOT a real product name (the brand
# is a pure function of the user's wake word).
BRAND = "Nova-Agent"


def _rendered(pool: tuple[str, ...]) -> set[str]:
    """The pool templates as they are actually spoken (brand resolved)."""
    return {p.replace("{agent}", BRAND) for p in pool}


def _composer(
    provider: _FakeProvider | None = None,
    *,
    timeout_ms: int = 1500,
) -> SpawnAnnouncementComposer:
    if provider is None:
        return SpawnAnnouncementComposer(brand_provider=lambda: BRAND)
    cfg = AckBrainConfig(timeout_ms=timeout_ms)
    breaker = CircuitBreaker(threshold=3, cooldown_s=60)
    return SpawnAnnouncementComposer(
        provider=provider,
        config=cfg,
        breaker=breaker,
        brand_provider=lambda: BRAND,
    )


# --------------------------------------------------------------------------- #
# Fallback-only mode (no provider wired — e.g. [ack_brain] disabled)          #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_fallback_only_returns_de_pool_phrase() -> None:
    composer = _composer()
    out = await composer.compose(
        utterance="Schau bitte in meine Mails, ich warte auf eine Rechnung."
    )
    assert out in _rendered(_FALLBACK_SPAWN["de"])


@pytest.mark.asyncio
async def test_fallback_only_detects_english_turn() -> None:
    composer = _composer()
    out = await composer.compose(
        utterance="Please check my Gmail inbox for new invoices."
    )
    assert out in _rendered(_FALLBACK_SPAWN["en"])


@pytest.mark.asyncio
async def test_explicit_language_overrides_heuristic() -> None:
    composer = _composer()
    out = await composer.compose(
        utterance="Schau bitte in mein Gmail rein.", language="en"
    )
    assert out in _rendered(_FALLBACK_SPAWN["en"])


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
    out_es = await composer.compose(
        utterance="Revisa mi correo, por favor.",
        language="es",
        kind="already_running",
    )
    assert out_de in _rendered(_FALLBACK_ALREADY_RUNNING["de"])
    assert out_en in _rendered(_FALLBACK_ALREADY_RUNNING["en"])
    assert out_es in _rendered(_FALLBACK_ALREADY_RUNNING["es"])


@pytest.mark.asyncio
async def test_spanish_turn_uses_pool_and_skips_llm() -> None:
    """An 'es' turn has no native persona — it must serve the curated Spanish
    pool directly and never spend an LLM round-trip that would only reject."""
    provider = _FakeProvider(reply="LLM must not run for an es turn.")
    composer = _composer(provider)
    out = await composer.compose(
        utterance="Por favor, revisa mi correo en busca de facturas.",
        language="es",
    )
    assert out in _rendered(_FALLBACK_SPAWN["es"])
    assert provider.calls == [], "es turn must skip the de/en-persona LLM path"


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
            spoken = phrase.replace("{agent}", BRAND)
            assert composer._validate(spoken, lang, BRAND), (
                f"pool phrase fails own validation ({lang}): {spoken!r}"
            )
            assert len(spoken) <= 120, f"pool phrase too long: {spoken!r}"
            assert "im Hintergrund darum" not in phrase
            assert "komplexe Aufgabe" not in phrase
            assert "vom User beschriebenen Workflow" not in phrase


def test_agent_status_pools_name_the_agent_brand_exactly_once() -> None:
    """Spawn and duplicate-status fallbacks must identify the delegated actor.

    The phrasing may rotate freely, but it must never leave the user guessing
    whether the assistant is merely thinking or has started a background
    agent. The pools carry the ``{agent}`` template exactly once so the
    rendered phrase names the brand exactly once, for ANY wake word.
    """
    for pool_by_language in (_FALLBACK_SPAWN, _FALLBACK_ALREADY_RUNNING):
        for pool in pool_by_language.values():
            for phrase in pool:
                assert phrase.lower().count("{agent}") == 1, phrase


def test_es_pools_survive_voice_scrubbing() -> None:
    """The Spanish pools are returned deterministically (never through
    ``_validate`` — ``_detect_language`` only knows de/en/unknown), so guard the
    real concern directly: every es phrase must survive ``scrub_for_voice``
    intact (TTS-clean, not gutted to empty)."""
    from jarvis.brain.output_filter import scrub_for_voice

    es_pools = (
        _FALLBACK_SPAWN["es"],
        _FALLBACK_ALREADY_RUNNING["es"],
        STILL_RUNNING_PHRASES["es"],
    )
    for pool in es_pools:
        for phrase in pool:
            cleaned = scrub_for_voice(
                phrase, language="es", ack_mode=True
            ).cleaned.strip()
            assert sum(c.isalnum() for c in cleaned) >= 3, (
                f"es phrase gutted by scrub_for_voice: {phrase!r} -> {cleaned!r}"
            )


def test_pools_have_enough_distinct_variants() -> None:
    for lang in ("de", "en", "es"):
        pool = _FALLBACK_SPAWN[lang]
        assert len(pool) >= 6
        assert len(set(pool)) == len(pool)
    for lang in ("de", "en", "es"):
        pool = _FALLBACK_ALREADY_RUNNING[lang]
        assert len(pool) >= 3
        assert len(set(pool)) == len(pool)


# Effort/time cues, per language, that prove the spawn pool conveys "this is a
# bigger task that takes a moment" (the 2026-06-19 sharpening) rather than a
# flat "on it". Each pool phrase must carry at least one cue.
_SUBSTANCE_CUES: dict[str, tuple[str, ...]] = {
    "de": (
        "grösser", "moment", "stück arbeit", "umfangreich", "gründlich",
        "mehr dahinter", "in ruhe", "braucht etwas",
    ),
    "en": (
        "bigger", "moment", "meatier", "more involved", "little time",
        "digging", "a bit more", "short", "solid",
    ),
    "es": (
        "más grande", "momento", "chicha", "más de trabajo", "poco de tiempo",
        "a fondo", "algo más", "más amplio", "momentito", "buen vistazo", "sólido",
    ),
}


def test_spawn_pools_convey_substance() -> None:
    """Every spawn-pool phrase signals a bigger task / that it takes time."""
    for lang, cues in _SUBSTANCE_CUES.items():
        for phrase in _FALLBACK_SPAWN[lang]:
            low = phrase.lower()
            assert any(cue in low for cue in cues), (
                f"spawn phrase lacks a 'bigger task / takes time' cue "
                f"({lang}): {phrase!r}"
            )


def test_still_running_phrases_cover_all_languages() -> None:
    """The heartbeat pool covers de/en/es with several distinct variants and
    never claims completion (the mission is still in flight)."""
    assert set(STILL_RUNNING_PHRASES) == {"de", "en", "es"}
    for lang, pool in STILL_RUNNING_PHRASES.items():
        assert len(pool) >= 4, f"too few heartbeat variants for {lang}"
        assert len(set(pool)) == len(pool)
        for phrase in pool:
            low = phrase.lower()
            assert not low.startswith(("erledigt", "fertig", "done", "listo")), (
                f"heartbeat must not open with a completion claim: {phrase!r}"
            )


# --------------------------------------------------------------------------- #
# LLM path                                                                    #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_provider_text_is_used_when_valid() -> None:
    provider = _FakeProvider(
        reply="Ein Nova-Agent schaut gleich in dein Gmail und sagt dir Bescheid."
    )
    composer = _composer(provider)
    out = await composer.compose(
        utterance="Schau bitte in mein Gmail rein.", language="de"
    )
    assert "Gmail" in out
    assert out not in _rendered(_FALLBACK_SPAWN["de"])
    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_provider_gets_de_persona_for_german_turn() -> None:
    """Language unset → composer detects German from the utterance."""
    provider = _FakeProvider(reply="Ein Nova-Agent schaut gleich in dein Gmail rein.")
    composer = _composer(provider)
    await composer.compose(
        utterance="Schau bitte nach, ob die Rechnung schon da ist und was drinsteht."
    )
    assert provider.calls[0]["persona_prompt"] == get_spawn_persona("de", BRAND)
    assert BRAND in provider.calls[0]["persona_prompt"]
    assert "{agent}" not in provider.calls[0]["persona_prompt"]


@pytest.mark.asyncio
async def test_provider_gets_en_persona_for_english_turn() -> None:
    provider = _FakeProvider(
        reply="A Nova-Agent is checking your Gmail in the background now."
    )
    composer = _composer(provider)
    await composer.compose(
        utterance="Check my Gmail for new mail.", language="en"
    )
    assert provider.calls[0]["persona_prompt"] == get_spawn_persona("en", BRAND)


@pytest.mark.asyncio
async def test_provider_content_includes_interpreted_action() -> None:
    provider = _FakeProvider(
        reply="Ein Nova-Agent prüft gleich deine Gmail-Inbox."
    )
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
    assert out in _rendered(_FALLBACK_SPAWN["de"])


@pytest.mark.asyncio
async def test_provider_error_falls_back() -> None:
    composer = _composer(_FakeProvider(raises=True))
    out = await composer.compose(
        utterance="Schau in mein Gmail.", language="de"
    )
    assert out in _rendered(_FALLBACK_SPAWN["de"])


@pytest.mark.asyncio
async def test_provider_empty_reply_falls_back() -> None:
    composer = _composer(_FakeProvider(reply="   "))
    out = await composer.compose(
        utterance="Schau in mein Gmail.", language="de"
    )
    assert out in _rendered(_FALLBACK_SPAWN["de"])


@pytest.mark.asyncio
async def test_overlong_reply_is_trimmed_to_leading_sentences() -> None:
    """Two sentences, the second pushing past the word cap: keep sentence 1."""
    long_tail = " ".join(["und"] * 30)
    provider = _FakeProvider(
        reply=f"Ein Nova-Agent schaut kurz in dein Gmail. Danach {long_tail}."
    )
    composer = _composer(provider)
    out = await composer.compose(
        utterance="Schau in mein Gmail.", language="de"
    )
    assert out == "Ein Nova-Agent schaut kurz in dein Gmail."


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
    assert out in _rendered(_FALLBACK_SPAWN["de"])


@pytest.mark.asyncio
async def test_language_mismatch_falls_back() -> None:
    provider = _FakeProvider(reply="Checking your Gmail right now for you.")
    composer = _composer(provider)
    out = await composer.compose(
        utterance="Schau in mein Gmail.", language="de"
    )
    assert out in _rendered(_FALLBACK_SPAWN["de"])


@pytest.mark.asyncio
async def test_completion_claim_is_rejected() -> None:
    """The worker has not even started — 'done' claims must never be spoken."""
    provider = _FakeProvider(reply="Die Aufgabe ist erledigt.")
    composer = _composer(provider)
    out = await composer.compose(
        utterance="Schau in mein Gmail.", language="de"
    )
    assert out in _rendered(_FALLBACK_SPAWN["de"])


@pytest.mark.asyncio
async def test_internal_component_names_are_rejected() -> None:
    provider = _FakeProvider(
        reply="Ich starte einen OpenClaw-Subagent für dein Gmail."
    )
    composer = _composer(provider)
    out = await composer.compose(
        utterance="Schau in mein Gmail.", language="de"
    )
    assert out in _rendered(_FALLBACK_SPAWN["de"])


@pytest.mark.asyncio
async def test_open_breaker_skips_provider() -> None:
    provider = _FakeProvider(reply="Ich schaue in dein Gmail.")
    cfg = AckBrainConfig(timeout_ms=1500)
    breaker = CircuitBreaker(threshold=1, cooldown_s=60)
    await breaker.record_failure()  # opens immediately at threshold=1
    composer = SpawnAnnouncementComposer(
        provider=provider, config=cfg, breaker=breaker,
        brand_provider=lambda: BRAND,
    )
    out = await composer.compose(
        utterance="Schau in mein Gmail.", language="de"
    )
    assert provider.calls == []
    assert out in _rendered(_FALLBACK_SPAWN["de"])


# --------------------------------------------------------------------------- #
# Failover provider (primary exhausted → live secondary, not the pool)        #
# --------------------------------------------------------------------------- #
# Mirrors the pre-thinking ack's Gemini→Grok failover
# (jarvis.brain.factory._build_ack_fallback). Root cause of the 2026-06-21
# "contextless stock phrase" report: the primary flash provider (gemini) was
# billing-exhausted (429 → None) and the spawn announcer had NO failover, so it
# degraded straight to the generic pool while a healthy grok was available.


def _composer_with_fallback(
    primary: _FakeProvider,
    fallback: _FakeProvider,
    *,
    timeout_ms: int = 1500,
    primary_breaker: CircuitBreaker | None = None,
) -> SpawnAnnouncementComposer:
    cfg = AckBrainConfig(timeout_ms=timeout_ms)
    return SpawnAnnouncementComposer(
        provider=primary,
        config=cfg,
        breaker=primary_breaker or CircuitBreaker(threshold=3, cooldown_s=60),
        fallback_provider=fallback,
        fallback_breaker=CircuitBreaker(threshold=3, cooldown_s=60),
        brand_provider=lambda: BRAND,
    )


@pytest.mark.asyncio
async def test_fallback_provider_used_when_primary_exhausted() -> None:
    """Primary returns nothing (e.g. a 429-exhausted adapter yields None) →
    the live failover provider's context-aware text is spoken, NOT a canned
    pool phrase. Without this, a dead primary silently degrades every spawn
    announcement to a generic stock line."""
    primary = _FakeProvider(reply=None)
    fallback = _FakeProvider(
        reply="Ein Nova-Agent übernimmt gerade das Thema Gmail."
    )
    composer = _composer_with_fallback(primary, fallback)
    out = await composer.compose(
        utterance="Schau bitte in mein Gmail rein.", language="de"
    )
    assert "Gmail" in out
    assert out not in _rendered(_FALLBACK_SPAWN["de"])
    assert len(primary.calls) == 1
    assert len(fallback.calls) == 1


@pytest.mark.asyncio
async def test_fallback_provider_used_when_primary_errors() -> None:
    primary = _FakeProvider(raises=True)
    fallback = _FakeProvider(
        reply="Ein Nova-Agent kümmert sich gleich um dein Gmail."
    )
    composer = _composer_with_fallback(primary, fallback)
    out = await composer.compose(
        utterance="Schau in mein Gmail.", language="de"
    )
    assert out == "Ein Nova-Agent kümmert sich gleich um dein Gmail."
    assert len(fallback.calls) == 1


@pytest.mark.asyncio
async def test_fallback_provider_used_when_primary_times_out() -> None:
    primary = _FakeProvider(reply="Ich schaue in dein Gmail.", delay_s=0.5)
    fallback = _FakeProvider(
        reply="Ein Nova-Agent nimmt sich dein Gmail gleich vor."
    )
    composer = _composer_with_fallback(primary, fallback, timeout_ms=100)
    out = await composer.compose(
        utterance="Schau in mein Gmail.", language="de"
    )
    assert out == "Ein Nova-Agent nimmt sich dein Gmail gleich vor."


@pytest.mark.asyncio
async def test_primary_success_skips_fallback() -> None:
    primary = _FakeProvider(
        reply="Ein Nova-Agent schaut gleich in dein Gmail."
    )
    fallback = _FakeProvider(reply="should never be reached")
    composer = _composer_with_fallback(primary, fallback)
    out = await composer.compose(
        utterance="Schau in mein Gmail.", language="de"
    )
    assert out == "Ein Nova-Agent schaut gleich in dein Gmail."
    assert fallback.calls == []


@pytest.mark.asyncio
async def test_both_providers_dead_falls_back_to_pool() -> None:
    primary = _FakeProvider(reply=None)
    fallback = _FakeProvider(reply=None)
    composer = _composer_with_fallback(primary, fallback)
    out = await composer.compose(
        utterance="Schau in mein Gmail.", language="de"
    )
    assert out in _rendered(_FALLBACK_SPAWN["de"])
    assert len(primary.calls) == 1
    assert len(fallback.calls) == 1


@pytest.mark.asyncio
async def test_open_primary_breaker_still_consults_fallback() -> None:
    """An open primary breaker (dead provider already tripped it) must not kill
    the context-aware path — the failover is still consulted before the pool."""
    primary = _FakeProvider(reply="primary text")
    fallback = _FakeProvider(
        reply="Ein Nova-Agent nimmt sich dein Gmail gleich vor."
    )
    primary_breaker = CircuitBreaker(threshold=1, cooldown_s=60)
    await primary_breaker.record_failure()  # opens immediately at threshold=1
    composer = _composer_with_fallback(
        primary, fallback, primary_breaker=primary_breaker
    )
    out = await composer.compose(
        utterance="Schau in mein Gmail.", language="de"
    )
    assert primary.calls == []  # primary skipped (breaker open)
    assert out == "Ein Nova-Agent nimmt sich dein Gmail gleich vor."
    assert len(fallback.calls) == 1


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
        candidate="Ein Nova-Agent geht gleich durch dein Gmail und meldet sich.",
    )
    assert "Gmail" in out
    assert provider.calls == []


@pytest.mark.asyncio
async def test_invalid_candidate_falls_through_to_llm() -> None:
    provider = _FakeProvider(
        reply="Ein Nova-Agent schaut gleich in dein Gmail rein."
    )
    composer = _composer(provider)
    out = await composer.compose(
        utterance="Schau in mein Gmail.",
        language="de",
        candidate="Erledigt.",  # completion claim — must be rejected
    )
    assert out == "Ein Nova-Agent schaut gleich in dein Gmail rein."
    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_candidate_without_public_agent_label_is_rejected() -> None:
    """Natural but ambiguous background wording must not bypass the contract."""
    provider = _FakeProvider(
        reply="Ein Nova-Agent schaut gründlich in dein Gmail."
    )
    composer = _composer(provider)

    out = await composer.compose(
        utterance="Schau in mein Gmail.",
        language="de",
        candidate="Ich kümmere mich im Hintergrund darum und melde mich.",
    )

    assert out == "Ein Nova-Agent schaut gründlich in dein Gmail."
    assert len(provider.calls) == 1


# --------------------------------------------------------------------------- #
# Hard guarantees                                                             #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_never_raises_and_never_empty_under_pathology() -> None:
    composer = _composer(_FakeProvider(raises=True))
    out = await composer.compose(utterance="", language=None, candidate=None)
    assert isinstance(out, str) and out.strip()


# --------------------------------------------------------------------------- #
# Wake-word-derived brand (2026-07-17 rebrand)                                 #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@pytest.mark.parametrize("brand", ["Ruben-Agent", "Harald-Agent", "Athena-Agent"])
async def test_brand_follows_any_wake_word_name(brand: str) -> None:
    """The spoken brand is a pure function of the configured wake word: a
    candidate naming the CURRENT brand passes, and every fallback phrase
    speaks that brand — for ANY name, never one blessed value."""
    composer = SpawnAnnouncementComposer(brand_provider=lambda: brand)
    out = await composer.compose(
        utterance="Schau in mein Gmail.",
        language="de",
        candidate=f"Ein {brand} geht gleich durch dein Gmail und meldet sich.",
    )
    assert brand in out

    pooled = await composer.compose(
        utterance="Check my Gmail please.", language="en"
    )
    assert brand in pooled
    assert "{agent}" not in pooled


@pytest.mark.asyncio
async def test_candidate_with_stale_brand_is_rejected() -> None:
    """A candidate naming a DIFFERENT (e.g. stale pre-rename) brand must not
    pass validation — the announcement re-composes with the live brand."""
    composer = SpawnAnnouncementComposer(brand_provider=lambda: "Ruben-Agent")
    out = await composer.compose(
        utterance="Schau in mein Gmail.",
        language="de",
        candidate="Ein Jarvis-Agent geht gleich durch dein Gmail.",
    )
    assert out in {p.replace("{agent}", "Ruben-Agent") for p in _FALLBACK_SPAWN["de"]}


@pytest.mark.asyncio
async def test_missing_brand_provider_uses_neutral_brand() -> None:
    """No provider wired (or a failing one) -> the neutral 'Assistant-Agent',
    never a trademarked product name."""
    composer = SpawnAnnouncementComposer()
    out = await composer.compose(utterance="Check my Gmail.", language="en")
    assert "Assistant-Agent" in out

    def _boom() -> str:
        raise RuntimeError("config unavailable")

    crashing = SpawnAnnouncementComposer(brand_provider=_boom)
    out2 = await crashing.compose(utterance="Check my Gmail.", language="en")
    assert "Assistant-Agent" in out2
