"""Tests for the configurable wake-word phrase matcher + engine resolver.

These pin the contract of ``jarvis/speech/wake_phrase.py`` and
``jarvis/speech/wake_constants.py`` — the core of the custom-wake-word feature.

The cardinal rule (see docs/local-wakeword/CUSTOM-WAKE-WORD-DESIGN.md):
- The DEFAULT "Hey Jarvis" phrase MUST reproduce the existing strict pattern
  byte-for-byte so the ~40 existing wake tests stay green.
- An ARBITRARY phrase ("Computer", "Athena") must match a noisy STT transcript
  with fuzzy tolerance, and never silently fail.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.speech import wake_constants as wc
from jarvis.speech import wake_phrase as wp
from jarvis.speech.wake_phrase import (
    compile_wake_matcher,
    resolve_wake_plan,
    sensitivity_to_threshold,
)


@pytest.fixture(autouse=True)
def _no_vosk_model(monkeypatch):
    """Isolate from any per-install Vosk model: this module pins the NON-vosk
    resolution chain (stt_match / none). The vosk_kws chain has its own
    dedicated suite in test_wake_plan_vosk.py."""
    monkeypatch.setattr(wp, "resolve_vosk_model_path", lambda *_: None)


# --------------------------------------------------------------------------
# WAKE_ENGINES single source of truth
# --------------------------------------------------------------------------

def test_wake_engines_are_the_five_canonical_engines() -> None:
    assert wc.WAKE_ENGINES == (
        "auto", "openwakeword", "vosk_kws", "stt_match", "custom_onnx"
    )


def test_default_wake_phrase_is_empty() -> None:
    # Shipped default is now blank (neutral); "Hey Jarvis" is still typeable.
    assert wc.DEFAULT_WAKE_PHRASE == ""


# --------------------------------------------------------------------------
# Matcher duck-types re.Pattern.search (drop-in replacement everywhere)
# --------------------------------------------------------------------------

def test_matcher_has_search_returning_group_like_re_pattern() -> None:
    m = compile_wake_matcher("Computer")
    hit = m.search("hey computer")
    assert hit is not None
    assert hit.group(0)  # truthy matched substring
    assert m.search("the weather is fine") is None


# --------------------------------------------------------------------------
# "Hey Jarvis" is an ordinary phrase on the generic matcher (design 2026-07-07)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("phrase", ["Hey Jarvis", "jarvis", "Jarvis", "hey jarvis"])
def test_jarvis_phrase_matches_prefixed_transcripts(phrase: str) -> None:
    m = compile_wake_matcher(phrase)
    assert m.search("hey jarvis") is not None
    assert m.search("hi jarvis") is not None
    assert m.search("hallo jarvis") is not None


def test_prefixed_jarvis_phrase_rejects_bare_word_and_hallucinations() -> None:
    # BUG-009 on the GENERIC matcher: a phrase that carries a wake prefix
    # ("Hey Jarvis") must NOT fire on the bare core word in ordinary speech,
    # and common Whisper hallucinations must never match.
    m = compile_wake_matcher("Hey Jarvis")
    assert m.search("jarvis") is None
    assert m.search("Thank you") is None
    assert m.search("Vielen Dank") is None


def test_single_word_jarvis_phrase_behaves_like_any_single_word() -> None:
    # A phrase WITHOUT a prefix is a one-word wake: the word itself fires
    # anywhere — identical to "Computer" (no special-cased word ships).
    m = compile_wake_matcher("jarvis")
    assert m.search("jarvis") is not None
    assert m.search("Thank you") is None


# --------------------------------------------------------------------------
# Arbitrary phrase — fuzzy STT-transcript matching
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "transcript",
    ["computer", "Computer.", "hey computer", "okay computer", "the computer is on"],
)
def test_arbitrary_single_word_phrase_matches(transcript: str) -> None:
    m = compile_wake_matcher("Computer")
    assert m.search(transcript) is not None, transcript


@pytest.mark.parametrize("transcript", ["banana", "the weather is nice", "come here"])
def test_arbitrary_single_word_phrase_rejects_unrelated(transcript: str) -> None:
    m = compile_wake_matcher("Computer")
    assert m.search(transcript) is None, transcript


def test_arbitrary_phrase_tolerates_transcription_drift() -> None:
    # Whisper drifts proper nouns: "Athena" -> "Athene"/"Atena". The fuzzy
    # matcher must still fire (ratio >= 0.8) so the word "actually works".
    m = compile_wake_matcher("Athena")
    assert m.search("athene") is not None
    assert m.search("atena") is not None
    assert m.search("hey athena") is not None


def test_arbitrary_phrase_tolerates_diacritic_transcription_drift() -> None:
    # Cloud/local STT may add or remove accents for names. A typed wake word
    # like "Alex" must still fire when the transcript comes back as "Alex".
    m = compile_wake_matcher("Alex")
    assert m.search("hey alex") is not None


def test_prefix_phrase_requires_the_prefix() -> None:
    # User mandate 2026-07-02 (REVERSES the 2026-06-29 "prefix optional"
    # trade-off): Jarvis kept activating on the bare core word inside ordinary
    # speech (live: 'WAKE matched fable in "1 Fable Pro"'; bench: 71.7 % false
    # accepts on real bare-core windows). A phrase configured WITH a prefix
    # fires ONLY when a wake prefix immediately precedes the core.
    m = compile_wake_matcher("Hey Athena")
    assert m.search("hey athena") is not None       # full phrase fires
    assert m.search("hallo athena") is not None      # prefix family counts
    assert m.search("athena") is None                # bare core stays silent
    assert m.search("i met athena today") is None    # core mid-sentence: silent
    assert m.search("the weather is nice") is None   # unrelated rejected


def test_hey_nico_fires_only_on_the_full_phrase() -> None:
    # The user's real-world case. Full phrase + STT spelling drift must wake;
    # the bare name in ordinary/dictated speech must NOT (that was the
    # "Jarvis spawns although I did not call it" bug, live-logged 2026-07-02).
    m = compile_wake_matcher("Hey Nico")
    assert m.search("hey nico") is not None        # full phrase
    assert m.search("hey niko") is not None         # STT drift (one char)
    assert m.search("hallo nico alles gut") is not None  # localised greeting
    assert m.search("nico") is None                 # bare name: silent
    assert m.search("ja nico komm") is None         # name mid-utterance: silent
    assert m.search("nico mein barsch") is None     # the live false fire: silent
    assert m.search("wie spät ist es") is None      # unrelated -> no wake  # i18n-allow


def test_multi_word_core_phrase_matches_in_order() -> None:
    m = compile_wake_matcher("Blue Sky")
    assert m.search("the blue sky today") is not None
    assert m.search("sky blue") is None  # wrong order


def test_fuzzy_ratio_is_configurable() -> None:
    strict = compile_wake_matcher("Athena", fuzzy_ratio=0.99)
    loose = compile_wake_matcher("Athena", fuzzy_ratio=0.6)
    assert strict.search("athene") is None      # too strict for the drift
    assert loose.search("athene") is not None


def test_short_custom_name_tolerates_one_char_pronunciation_drift() -> None:
    # Mission 2026-06-29: short proper-noun wake words ("Neko") are penalised
    # hardest by SequenceMatcher — a single STT mishearing ("Neko" -> "Niko")
    # drops a 4-char word to ratio 0.75, just under the 0.8 default, so the word
    # "never works". The matcher must allow ~one character of drift for short
    # cores so a normal pronunciation variance still wakes.
    m = compile_wake_matcher("Neko")
    assert m.search("niko") is not None
    assert m.search("neeko") is not None
    assert m.search("hey neko") is not None


def test_short_custom_name_still_rejects_unrelated_words() -> None:
    # The short-name relaxation must NOT become a hair-trigger: an unrelated
    # word (even a short one) must still be rejected (false-positive guard).
    m = compile_wake_matcher("Neko")
    assert m.search("taco") is None
    assert m.search("hello there") is None
    assert m.search("the cat sat down") is None


def test_long_phrase_keeps_strict_ratio_for_short_name_relaxation() -> None:
    # The relaxation is length-aware: it only loosens SHORT cores. A long core
    # word keeps the configured ratio, so an explicit strict matcher on a long
    # word still rejects its drift (the configurable-ratio contract is intact).
    strict = compile_wake_matcher("Athena", fuzzy_ratio=0.99)
    assert strict.search("athene") is None


# --------------------------------------------------------------------------
# sensitivity -> OWW threshold mapping (anchored on PRODUCTION_WAKE_THRESHOLD)
# --------------------------------------------------------------------------

def test_default_sensitivity_maps_to_production_threshold() -> None:
    from jarvis.plugins.wake.openwakeword_provider import PRODUCTION_WAKE_THRESHOLD

    assert sensitivity_to_threshold(0.5) == pytest.approx(PRODUCTION_WAKE_THRESHOLD)


def test_sensitivity_mapping_is_monotonic_decreasing() -> None:
    # Higher sensitivity -> lower threshold -> easier to trigger.
    assert sensitivity_to_threshold(0.0) > sensitivity_to_threshold(0.5)
    assert sensitivity_to_threshold(0.5) > sensitivity_to_threshold(1.0)
    assert sensitivity_to_threshold(0.0) == pytest.approx(0.30)


def test_sensitivity_clamped_to_unit_interval() -> None:
    assert sensitivity_to_threshold(-1.0) == sensitivity_to_threshold(0.0)
    assert sensitivity_to_threshold(2.0) == sensitivity_to_threshold(1.0)


# --------------------------------------------------------------------------
# resolve_wake_plan — the engine-resolution brain
# --------------------------------------------------------------------------

def _cfg(**kw: object) -> SimpleNamespace:
    base = dict(
        phrase="Hey Jarvis",
        engine="auto",
        custom_model_path="",
        sensitivity=0.5,
        fuzzy_match_ratio=0.8,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _cfg_blank(**kw: object) -> SimpleNamespace:
    """Config with empty phrase — the new shipped default pre-onboarding state."""
    base = dict(
        phrase="",
        engine="auto",
        custom_model_path="",
        sensitivity=0.5,
        fuzzy_match_ratio=0.8,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_jarvis_phrase_resolves_generically_not_to_a_bundled_model() -> None:
    """Design 2026-07-07: no pretrained brand models — 'Hey Jarvis' is just a phrase."""
    plan = resolve_wake_plan(_cfg(), local_whisper_available=True)
    assert plan.engine == "stt_match"  # vosk is fenced off by the autouse fixture
    assert plan.oww_model_path is None
    assert plan.oww_keyword == "jarvis"
    # Task 5 (B3): an ordinary custom phrase served ONLY by stt_match is now a
    # LOUD degrade (AP-27) — no Vosk model, no custom ONNX in this fixture.
    assert plan.degraded is True


def test_brand_phrase_never_loads_an_upstream_package_model() -> None:
    """Typing a third-party brand word must NOT pull that brand's model."""
    for phrase in ("Alexa", "Hey Mycroft", "Rhasspy"):
        plan = resolve_wake_plan(_cfg(phrase=phrase), local_whisper_available=True)
        assert plan.engine == "stt_match", phrase
        assert plan.oww_model_path is None, phrase


def test_jarvis_phrase_without_any_local_engine_is_hotkey_only() -> None:
    """No bundled model means the jarvis phrase degrades like any other word."""
    plan = resolve_wake_plan(_cfg(), local_whisper_available=False)
    assert plan.wake_available is False
    assert plan.engine == "none"
    assert plan.oww_model_path is None


def test_arbitrary_phrase_with_local_whisper_resolves_to_stt_match() -> None:
    plan = resolve_wake_plan(_cfg(phrase="Computer"), local_whisper_available=True)
    assert plan.engine == "stt_match"
    assert plan.needs_local_whisper is True
    assert plan.oww_model_path is None
    # Task 5 (B3): stt_match-only for an ordinary custom phrase is a LOUD
    # degrade (AP-27), not silent success.
    assert plan.degraded is True
    assert plan.matcher.search("hey computer") is not None


def test_arbitrary_phrase_without_local_whisper_is_hotkey_only() -> None:
    # Product rule (2026-07-04): no local model for the user's OWN word -> the
    # wake word is OFF (hotkey / push-to-talk activation), NOT a silent branded
    # 'Hey Rhasspy' fallback that listens for a word the user never says.
    plan = resolve_wake_plan(_cfg(phrase="Computer"), local_whisper_available=False)
    assert plan.wake_available is False
    assert plan.engine == "none"
    assert plan.oww_model_path is None
    assert plan.degraded is True
    assert "computer" in plan.message.lower()
    # The message must point the user at the real options AND the hotkey.
    assert "local" in plan.message.lower() or "onnx" in plan.message.lower()
    assert "hotkey" in plan.message.lower() or "push-to-talk" in plan.message.lower()


def test_wake_available_reflects_whether_a_local_model_exists() -> None:
    # The product rule in one test: an arbitrary word works IF a local model is
    # available (stt_match via local Whisper), and is hotkey-only otherwise —
    # never a silent branded substitute.
    with_model = resolve_wake_plan(_cfg(phrase="Nebula"), local_whisper_available=True)
    assert with_model.engine == "stt_match"
    assert with_model.wake_available is True
    without = resolve_wake_plan(_cfg(phrase="Nebula"), local_whisper_available=False)
    assert without.engine == "none"
    assert without.wake_available is False


def test_explicit_custom_onnx_loads_user_model(tmp_path: object) -> None:
    model = tmp_path / "my_wake.onnx"  # type: ignore[operator]
    model.write_bytes(b"\x00")
    plan = resolve_wake_plan(
        _cfg(phrase="Friday", engine="custom_onnx", custom_model_path=str(model)),
        local_whisper_available=False,
    )
    assert plan.engine == "custom_onnx"
    assert plan.oww_model_path == str(model)
    assert plan.degraded is False


def test_custom_onnx_missing_file_degrades_to_stt_match_when_whisper_present(
    tmp_path: object,
) -> None:
    plan = resolve_wake_plan(
        _cfg(
            phrase="Friday",
            engine="custom_onnx",
            custom_model_path=str(tmp_path / "missing.onnx"),  # type: ignore[operator]
        ),
        local_whisper_available=True,
    )
    assert plan.engine == "stt_match"
    assert plan.degraded is True


def test_blank_phrase_without_whisper_is_hotkey_only() -> None:
    # Blank phrase (shipped default / pre-onboarding) + no local model -> wake OFF
    # (hotkey-only), never a branded fallback.
    plan = resolve_wake_plan(_cfg(phrase="  "), local_whisper_available=False)
    assert plan.wake_available is False
    assert plan.engine == "none"
    assert plan.degraded is True


def test_blank_cfg_without_whisper_is_hotkey_only() -> None:
    plan = resolve_wake_plan(_cfg_blank(), local_whisper_available=False)
    assert plan.wake_available is False
    assert plan.engine == "none"
    assert plan.degraded is True


def test_jarvis_stays_typeable_through_the_generic_chain() -> None:
    # A user who explicitly types "Hey Jarvis" gets a working wake word via
    # the generic chain — no bundled model, no special case (design 2026-07-07).
    plan = resolve_wake_plan(_cfg(phrase="Hey Jarvis"), local_whisper_available=True)
    assert plan.engine == "stt_match"
    assert plan.oww_model_path is None
    # Task 5 (B3): stt_match-only is now a LOUD degrade (AP-27).
    assert plan.degraded is True


def test_just_jarvis_stays_typeable_through_the_generic_chain() -> None:
    # Bare "Jarvis" (no "Hey" prefix) is an ordinary single-word phrase now.
    plan = resolve_wake_plan(_cfg(phrase="Jarvis"), local_whisper_available=True)
    assert plan.engine == "stt_match"
    assert plan.oww_model_path is None
    # Task 5 (B3): stt_match-only is now a LOUD degrade (AP-27).
    assert plan.degraded is True


def test_sensitivity_to_poll_interval_makes_the_slider_control_speed() -> None:
    # The Sensitivity slider was a no-op on the stt_match (local-Whisper) path:
    # it only fed the openWakeWord threshold, which that path never scores
    # against. It now drives the poll interval, so moving it changes how fast a
    # spoken wake is picked up. Higher sensitivity => shorter interval => snappier.
    # Both ends are fast ("always as low as possible"); the slider only trims.
    assert wp.sensitivity_to_poll_interval(0.0) == pytest.approx(0.12)
    assert wp.sensitivity_to_poll_interval(1.0) == pytest.approx(0.08)
    assert wp.sensitivity_to_poll_interval(0.5) == pytest.approx(0.10)
    # strictly monotonic decreasing
    assert wp.sensitivity_to_poll_interval(0.2) > wp.sensitivity_to_poll_interval(0.8)
    # clamps out-of-range input instead of producing absurd intervals
    assert wp.sensitivity_to_poll_interval(-5) == wp.sensitivity_to_poll_interval(0.0)
    assert wp.sensitivity_to_poll_interval(9) == wp.sensitivity_to_poll_interval(1.0)
