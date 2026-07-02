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

# --------------------------------------------------------------------------
# WAKE_ENGINES single source of truth
# --------------------------------------------------------------------------

def test_wake_engines_are_the_four_canonical_engines() -> None:
    assert wc.WAKE_ENGINES == ("auto", "openwakeword", "stt_match", "custom_onnx")


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
# Default "Hey Jarvis" reproduces the strict legacy pattern
# --------------------------------------------------------------------------

@pytest.mark.parametrize("phrase", ["Hey Jarvis", "jarvis", "Jarvis", "hey jarvis"])
def test_jarvis_family_matches_strict_prefix_phrases(phrase: str) -> None:
    m = compile_wake_matcher(phrase)
    assert m.search("hey jarvis") is not None
    assert m.search("hi jarvis") is not None
    assert m.search("hallo jarvis") is not None


@pytest.mark.parametrize("phrase", ["Hey Jarvis", "jarvis"])
def test_jarvis_family_rejects_bare_word_and_hallucinations(phrase: str) -> None:
    # BUG-009: bare "jarvis" without a hey/hi/hallo prefix must NOT trigger,
    # and common Whisper hallucinations must not match.
    m = compile_wake_matcher(phrase)
    assert m.search("jarvis") is None
    assert m.search("Thank you") is None
    assert m.search("Vielen Dank") is None


def test_jarvis_matcher_is_backed_by_the_canonical_pattern() -> None:
    # The jarvis family must delegate to the single-source pattern so it can
    # never drift from rolling_whisper_wake.DEFAULT_PATTERN (BUG-008 territory).
    m = compile_wake_matcher("Hey Jarvis")
    assert m.is_jarvis_default is True


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
    # like "Ruben" must still fire when the transcript comes back as "Rubén".
    m = compile_wake_matcher("Ruben")
    assert m.search("hey rubén") is not None


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


def _pretend_oww_models_exist(
    monkeypatch: pytest.MonkeyPatch, *model_names: str
) -> None:
    """Make package-model tests independent of the local openwakeword install."""
    models = set(model_names)
    original_resolve = wc.resolve_oww_model_path

    def fake_resolve(model_name: str) -> str | None:
        if model_name in models:
            return f"C:/fake-openwakeword/{model_name}_v0.1.onnx"
        return original_resolve(model_name)

    monkeypatch.setattr(wc, "resolve_oww_model_path", fake_resolve)
    monkeypatch.setattr(wp, "resolve_oww_model_path", fake_resolve)


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


def test_default_phrase_resolves_to_bundled_hey_jarvis_oww() -> None:
    plan = resolve_wake_plan(_cfg(), local_whisper_available=False)
    assert plan.engine == "openwakeword"
    assert plan.oww_keyword == "hey_jarvis"
    assert plan.oww_model_path is not None
    assert plan.oww_model_path.endswith("hey_jarvis_v0.1.onnx")
    assert plan.needs_local_whisper is False
    assert plan.degraded is False


def test_known_pretrained_phrase_resolves_to_that_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _pretend_oww_models_exist(monkeypatch, "alexa")

    plan = resolve_wake_plan(_cfg(phrase="Alexa"), local_whisper_available=False)
    assert plan.engine == "openwakeword"
    assert plan.oww_keyword == "alexa"
    assert plan.oww_model_path is not None
    assert plan.oww_model_path.endswith("alexa_v0.1.onnx")
    assert plan.needs_local_whisper is False
    assert plan.degraded is False


def test_mycroft_and_rhasspy_resolve_to_pretrained_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _pretend_oww_models_exist(monkeypatch, "hey_mycroft")

    p1 = resolve_wake_plan(_cfg(phrase="Hey Mycroft"), local_whisper_available=False)
    assert p1.oww_keyword == "hey_mycroft"
    p2 = resolve_wake_plan(_cfg(phrase="Rhasspy"), local_whisper_available=False)
    assert p2.oww_keyword == "hey_rhasspy"


def test_arbitrary_phrase_with_local_whisper_resolves_to_stt_match() -> None:
    plan = resolve_wake_plan(_cfg(phrase="Computer"), local_whisper_available=True)
    assert plan.engine == "stt_match"
    assert plan.needs_local_whisper is True
    assert plan.oww_model_path is None
    assert plan.degraded is False
    assert plan.matcher.search("hey computer") is not None


def test_arbitrary_phrase_without_local_whisper_degrades_to_rhasspy() -> None:
    # Path D now falls back to the bundled hey_rhasspy model (neutral default).
    plan = resolve_wake_plan(_cfg(phrase="Computer"), local_whisper_available=False)
    assert plan.engine == "openwakeword"
    assert plan.oww_keyword == "hey_rhasspy"
    assert plan.degraded is True
    assert plan.verify_prefix is False   # rhasspy model IS the discriminator
    assert "computer" in plan.message.lower()
    # The degraded message must point the user at the real options.
    assert "whisper" in plan.message.lower() or "onnx" in plan.message.lower()


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


def test_blank_phrase_degrades_to_rhasspy() -> None:
    # Blank phrase (shipped default / pre-onboarding) now degrades to hey_rhasspy.
    plan = resolve_wake_plan(_cfg(phrase="  "), local_whisper_available=False)
    assert plan.oww_keyword == "hey_rhasspy"
    assert plan.engine == "openwakeword"
    assert plan.degraded is True
    assert plan.verify_prefix is False


def test_blank_phrase_without_whisper_degrades_to_rhasspy() -> None:
    plan = resolve_wake_plan(_cfg_blank(), local_whisper_available=False)
    assert plan.oww_keyword == "hey_rhasspy"
    assert plan.degraded is True
    assert plan.verify_prefix is False


def test_jarvis_stays_typeable_resolves_to_hey_jarvis_model() -> None:
    # A user who explicitly types "Hey Jarvis" must still get the hey_jarvis
    # OWW model (verify_prefix=True, not degraded).
    plan = resolve_wake_plan(_cfg(phrase="Hey Jarvis"), local_whisper_available=False)
    assert plan.engine == "openwakeword"
    assert plan.oww_keyword == "hey_jarvis"
    assert plan.degraded is False
    assert plan.verify_prefix is True


def test_just_jarvis_stays_typeable_resolves_to_hey_jarvis_model() -> None:
    # Bare "Jarvis" (no "Hey" prefix) also maps to the hey_jarvis model.
    plan = resolve_wake_plan(_cfg(phrase="Jarvis"), local_whisper_available=False)
    assert plan.engine == "openwakeword"
    assert plan.oww_keyword == "hey_jarvis"
    assert plan.degraded is False
    assert plan.verify_prefix is True
