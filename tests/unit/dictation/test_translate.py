"""The translate pass: speak one language, have the text arrive in another.

The feature reuses the polish pass's whole machine — chain, breaker, ceiling,
fail-open contract — and changes exactly three things: the prompt, the guard
set, and the status. Each of those three has a way of half-landing that no
other test in this directory would notice, and this file pins all of them:

* **The guard set must be the translate one.** ``drift_reason`` rejects a
  translation on ``language_flip`` by design — it exists to stop the polish pass
  translating — so wiring the wrong guard would reject 100 % of correct answers
  while looking like a working feature with a mysteriously high rejection rate.
  ``test_the_polish_guard_would_have_rejected_this_translation`` states that
  trap out loud rather than leaving it as folklore.
* **It must be ONE model call.** The pass sits between the key release and the
  words appearing; chaining polish-then-translate would silently double the
  wait for the users who turn it on.
* **The word floor must not apply.** Skipping the formatter on a four-word
  dictation is invisible; skipping the TRANSLATION on it delivers the wrong
  language into a live document, which reads as a broken feature rather than a
  tuned one.

The model is faked at ``build_polish_client`` — the same seam
``test_polish_chain`` uses — so the prompt that would really have gone over the
wire is what these tests read.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from jarvis.core import config as jarvis_config
from jarvis.core.config import DictationConfig
from jarvis.dictation import polish as polish_module
from jarvis.dictation.polish import (
    POLISH_STATUSES,
    polish_transcript,
    reset_polish_state,
    resolve_translate_target,
    translate_enabled,
)
from jarvis.dictation.polish_client import POLISH_FAMILIES, resolve_polish_chain
from jarvis.dictation.polish_guards import (
    TRANSLATE_DRIFT_REASONS,
    drift_reason,
    translate_drift_reason,
)
from jarvis.dictation.polish_prompt import RAW_OPEN_DELIMITER

# A real German dictation and the English a competent model returns for it.
# Deliberately long enough to clear the polish pass's four-word floor, so a test
# that finds the floor applying knows it was applied on purpose.
#
# The German below is the MATERIAL under test, not prose: a translation test
# needs text in a language other than the target, and the language detector the
# guards run on knows de/en/es — so the fixture has to be one of those to
# exercise the check at all (CLAUDE.md §1, allowed category 4).
GERMAN = (
    "also ich glaube wir sollten den bericht am dienstag rausschicken"  # i18n-allow
)
ENGLISH = "I think we should send the report out on Tuesday."
# What the PLAIN polish pass returns for the same input: same language, tidied.
POLISHED_GERMAN = (
    "Ich glaube, wir sollten den Bericht am Dienstag rausschicken."  # i18n-allow
)

# Four words: under the polish floor, over nothing.
SHORT_GERMAN = "hallo ich bin cool"  # i18n-allow: input under test
SHORT_ENGLISH = "Hello, I am cool."


@dataclass
class _FakeClient:
    """Answers with a fixed string and remembers what it was asked."""

    answer: str
    error: Exception | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def complete(
        self,
        system: str,
        user: str,
        *,
        max_output_tokens: int,
        temperature: float,
        timeout_s: float,
    ) -> str:
        self.calls.append({"system": system, "user": user, "timeout_s": timeout_s})
        if self.error is not None:
            raise self.error
        return self.answer


@pytest.fixture(autouse=True)
def _clean_state() -> None:
    """No breaker state, chain cache or credential memo leaks between tests."""
    reset_polish_state()
    yield
    reset_polish_state()


@pytest.fixture
def model(monkeypatch: pytest.MonkeyPatch):
    """A host holding exactly one key, whose model answers with what we set.

    Returns the client, so a test can read the prompt that was really built.
    """
    client = _FakeClient(answer="")

    # ``tests/unit/conftest.py`` empties the chain for every unit test so nobody
    # bills the developer's real key by finishing a dictation. A test that wants
    # the pass to RUN restores it in its own body — the documented escape, and
    # the reason this line is not a workaround.
    monkeypatch.setattr(
        polish_module, "resolve_polish_chain", resolve_polish_chain
    )
    # The slot name is DERIVED from the family rather than typed out: which
    # credential the first family reads is ``test_polish_chain``'s subject, not
    # this file's, and hard-coding it here would make these tests fail for a
    # reason that has nothing to do with translation.
    slot = POLISH_FAMILIES[0].secret_candidates[0]
    monkeypatch.setattr(
        jarvis_config,
        "get_secret",
        lambda key, env_fallback=None: "k" if key == slot else None,
    )
    monkeypatch.setattr(
        polish_module, "build_polish_client", lambda family, *, model: client
    )
    return client


def _cfg(**overrides: Any) -> DictationConfig:
    """A dictation config with history off and the pass configured explicitly."""
    base: dict[str, Any] = {
        "history_enabled": False,
        "polish": True,
        "polish_provider": "auto",
        # Generous, because these tests run against an in-process fake and a
        # tight ceiling would make them flaky on a loaded CI box rather than
        # meaningful.
        "polish_timeout_ms": 5000,
    }
    base.update(overrides)
    return DictationConfig(**base)


# --------------------------------------------------------------------------
# Which dictations get translated at all
# --------------------------------------------------------------------------


def test_the_switch_ships_off() -> None:
    """The one switch in this block that defaults off, and why.

    The polish pass may default on because it only changes how the user's words
    are written. This changes WHICH WORDS come out, so an install that never
    asked for it must never acquire it.
    """
    cfg = DictationConfig()
    assert cfg.translate is False
    assert translate_enabled(cfg) is False
    assert resolve_translate_target(cfg, "de") == ""


def test_a_pinned_source_equal_to_the_target_is_not_translated() -> None:
    """No round trip to turn English into English."""
    cfg = _cfg(translate=True, translate_target="en")
    assert resolve_translate_target(cfg, "en") == ""
    assert resolve_translate_target(cfg, "en-US") == ""
    # The cloud Whisper APIs answer with the language NAME, not a code. Reaching
    # the canonical resolver rather than slicing the string is what keeps that
    # from being read as an unknown language and paying for a translation of
    # English into English.
    assert resolve_translate_target(cfg, "English") == ""


def test_a_different_source_resolves_to_the_target() -> None:
    cfg = _cfg(translate=True, translate_target="en")
    assert resolve_translate_target(cfg, "de") == "en"
    assert resolve_translate_target(cfg, "German") == "en"


def test_an_unknown_source_still_attempts_the_translation() -> None:
    """Undecided means try, not skip.

    Guessing wrong this way costs one model call. Guessing wrong the other way
    leaves the text in the language the user was translating out of, which is
    the failure they would actually notice.
    """
    cfg = _cfg(translate=True, translate_target="en")
    assert resolve_translate_target(cfg, "auto") == "en"
    assert resolve_translate_target(cfg, "unknown") == "en"
    assert resolve_translate_target(cfg, "") == "en"


def test_a_hand_edited_target_falls_back_to_a_working_language() -> None:
    """AP-16: a typo in jarvis.toml costs a setting, never a boot."""
    assert DictationConfig(translate_target="klingon").translate_target == "en"
    assert DictationConfig(translate_target="").translate_target == "en"
    assert DictationConfig(translate_target="DE").translate_target == "de"


# --------------------------------------------------------------------------
# What the pass does with one
# --------------------------------------------------------------------------


async def test_a_translated_dictation_is_delivered_and_says_so(model) -> None:
    model.answer = ENGLISH
    result = await polish_transcript(
        GERMAN, language="de", cfg=_cfg(translate=True), translate_to="en"
    )

    assert result.text == ENGLISH
    assert result.status == "translated"
    assert result.status in POLISH_STATUSES
    assert result.reason == ""
    assert result.provider == "groq"


async def test_translating_and_formatting_are_one_model_call(model) -> None:
    """The latency contract. Two calls would double a wait the user feels."""
    model.answer = ENGLISH
    await polish_transcript(
        GERMAN, language="de", cfg=_cfg(translate=True), translate_to="en"
    )
    assert len(model.calls) == 1


async def test_the_translate_prompt_replaces_the_polish_one(model) -> None:
    """The two contracts are mutually exclusive and must not both be sent.

    The polish prompt's second hard rule is that the output language equals the
    input language. Sending it alongside a translation instruction is a prompt
    that contradicts itself, and the model resolves that however it likes.
    """
    model.answer = ENGLISH
    await polish_transcript(
        GERMAN, language="de", cfg=_cfg(translate=True), translate_to="en"
    )

    system = model.calls[0]["system"]
    assert "dictation translator" in system
    assert "OUTPUT LANGUAGE = INPUT LANGUAGE" not in system
    # The target is named in words, not left as a bare code for the model to
    # decode.
    assert "English" in system
    # The cleanup instructions ride along, because there is no second call.
    assert "filler" in system.lower()


async def test_the_transcript_still_travels_fenced_as_untrusted_material(
    model,
) -> None:
    """The injection defence is the polish pass's, unchanged.

    People dictate sentences shaped exactly like instructions — and in this
    feature, sentences shaped like instructions to a TRANSLATOR are especially
    likely ("translate the following into French"). The fence and the
    meta_output guard that watches for it are one mechanism, so the translate
    pass must not grow its own.
    """
    model.answer = ENGLISH
    await polish_transcript(
        GERMAN, language="de", cfg=_cfg(translate=True), translate_to="en"
    )

    call = model.calls[0]
    assert RAW_OPEN_DELIMITER in call["user"]
    assert GERMAN in call["user"]
    # The untrusted text never appears in the instruction half.
    assert GERMAN not in call["system"]


async def test_the_word_floor_does_not_apply_to_a_translation(model) -> None:
    """A four-word dictation is exactly where a half-working feature shows.

    Skipping the formatter here is invisible. Skipping the translation delivers
    German into an English document, and "it works on long ones" reads as a bug.
    """
    model.answer = SHORT_ENGLISH
    result = await polish_transcript(
        SHORT_GERMAN,
        language="de",
        cfg=_cfg(translate=True, polish_min_words=20),
        translate_to="en",
    )

    assert result.status == "translated"
    assert result.text == SHORT_ENGLISH


async def test_the_word_floor_still_applies_to_a_plain_polish(model) -> None:
    """The floor was not removed, only made inapplicable to translations."""
    model.answer = "should not be reached"
    result = await polish_transcript(
        SHORT_GERMAN, language="de", cfg=_cfg(polish_min_words=20)
    )

    assert result.status == "skipped_short"
    assert result.text == SHORT_GERMAN
    assert model.calls == []


async def test_a_translation_runs_even_with_the_wording_pass_switched_off(
    model,
) -> None:
    """"The formatter is off" is not an answer to "put this in English"."""
    model.answer = ENGLISH
    result = await polish_transcript(
        GERMAN,
        language="de",
        cfg=_cfg(polish=False, translate=True),
        translate_to="en",
    )

    assert result.status == "translated"
    assert result.text == ENGLISH


async def test_no_target_leaves_the_plain_polish_path_untouched(model) -> None:
    """The whole feature is inert while nobody asked for it.

    The answer stays in the input's language on purpose: with no target the
    polish contract applies in full, ``language_flip`` included, and an English
    answer here would be correctly rejected — which is the point of the
    assertion below rather than a detail of the fixture.
    """
    model.answer = POLISHED_GERMAN
    result = await polish_transcript(GERMAN, language="de", cfg=_cfg())

    assert result.status == "applied"
    assert result.text == POLISHED_GERMAN
    assert "dictation translator" not in model.calls[0]["system"]


# --------------------------------------------------------------------------
# When it goes wrong, the user keeps their words
# --------------------------------------------------------------------------


async def test_a_model_that_did_not_translate_is_rejected_not_celebrated(
    model,
) -> None:
    """The same string back is success for a formatter and failure here.

    Reporting it as ``unchanged`` would put "nothing needed doing" on the
    history row of a translation that never happened.
    """
    model.answer = GERMAN
    result = await polish_transcript(
        GERMAN, language="de", cfg=_cfg(translate=True), translate_to="en"
    )

    assert result.status == "rejected_drift"
    assert result.reason == "not_translated"
    assert result.reason in TRANSLATE_DRIFT_REASONS
    assert result.text == GERMAN


async def test_text_already_in_the_target_may_come_back_unchanged(model) -> None:
    """The one case where no change is the right answer, not a refusal.

    ``resolve_translate_target`` normally keeps a dictation that is already in
    the target off the translate path. It can only do that when the recognizer
    NAMED a language, though — on an undecided tag the text arrives here, and
    calling a clean English sentence bound for English a failed translation
    would invent a rejection out of a correct no-op.
    """
    model.answer = ENGLISH
    result = await polish_transcript(
        ENGLISH, language="unknown", cfg=_cfg(translate=True), translate_to="en"
    )

    assert result.status == "unchanged"
    assert result.text == ENGLISH


async def test_a_translation_into_the_wrong_language_is_rejected(model) -> None:
    model.answer = "Creo que deberiamos enviar el informe el martes."
    result = await polish_transcript(
        GERMAN, language="de", cfg=_cfg(translate=True), translate_to="en"
    )

    assert result.status == "rejected_drift"
    assert result.reason == "wrong_language"
    assert result.text == GERMAN


async def test_a_dead_provider_delivers_the_spoken_words(model) -> None:
    """Fail-open, exactly like the polish pass — visibly, unlike it.

    The words arrive in the language they were spoken in. That IS the fallback,
    and the status is how the history row explains it.
    """
    model.error = RuntimeError("502 upstream")
    result = await polish_transcript(
        GERMAN, language="de", cfg=_cfg(translate=True), translate_to="en"
    )

    assert result.text == GERMAN
    assert result.status == "provider_error"


async def test_no_key_anywhere_is_not_a_broken_dictation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AP-23: an install with no text-model key behaves as if unbuilt."""
    monkeypatch.setattr(
        jarvis_config, "get_secret", lambda key, env_fallback=None: None
    )
    result = await polish_transcript(
        GERMAN, language="de", cfg=_cfg(translate=True), translate_to="en"
    )

    assert result.text == GERMAN
    assert result.status == "unavailable"
    assert result.reason == "no_credential"


# --------------------------------------------------------------------------
# The guard set, and the trap of picking the wrong one
# --------------------------------------------------------------------------


def test_the_polish_guard_would_have_rejected_this_translation() -> None:
    """Why the translate pass cannot reuse ``drift_reason``.

    This is not a hypothetical: ``language_flip`` exists precisely to stop the
    polish pass translating, and the polish word-count band is tight enough to
    reject a faithful translation on length alone. A future refactor that
    "simplifies" the two guard sets into one would ship a feature that rejects
    every correct answer it produces, and the history would show nothing but
    ``rejected_drift``.
    """
    assert (
        drift_reason(
            GERMAN,
            ENGLISH,
            language="de",
            protected=(),
            max_shrink=0.55,
            max_growth=1.20,
        )
        == "language_flip"
    )
    assert (
        translate_drift_reason(
            GERMAN,
            ENGLISH,
            target_language="en",
            protected=(),
            max_shrink=0.40,
            max_growth=2.50,
        )
        == ""
    )


def test_numbers_and_names_must_survive_a_translation() -> None:
    """What still means something after every content word has changed."""
    kwargs: dict[str, Any] = {
        "target_language": "en",
        "max_shrink": 0.40,
        "max_growth": 2.50,
    }
    assert (
        translate_drift_reason(
            "wir treffen uns um 3 uhr am bahnhof mit anna",
            "We are meeting at the station with Anna.",
            protected=(),
            **kwargs,
        )
        == "lost_number"
    )
    assert (
        translate_drift_reason(
            GERMAN + " mit Nova",
            ENGLISH + " with Neuva",
            protected=("Nova",),
            **kwargs,
        )
        == "lost_term"
    )
    assert (
        translate_drift_reason(
            GERMAN + " mit Nova", ENGLISH + " with Nova", protected=("Nova",), **kwargs
        )
        == ""
    )


def test_a_model_that_answered_instead_of_translating_is_caught() -> None:
    assert (
        translate_drift_reason(
            GERMAN,
            "Here is the translation: I think we should send the report.",
            target_language="en",
            protected=(),
            max_shrink=0.40,
            max_growth=2.50,
        )
        == "meta_output"
    )


def test_a_target_the_detector_cannot_place_is_a_no_op_not_a_veto() -> None:
    """~96 of the 100 targets are undecidable; that must not mean "reject".

    The detector knows de/en/es. Vetoing everything else would make the feature
    work for three languages and silently refuse for the rest — the same
    asymmetry the rare-token filter documents, and the same answer: silence
    beats a veto, and the remaining guards still apply.
    """
    assert (
        translate_drift_reason(
            GERMAN,
            "Konnichiwa. Kayoubi ni repooto o okuru beki da to omoimasu.",
            target_language="ja",
            protected=(),
            max_shrink=0.40,
            max_growth=2.50,
        )
        == ""
    )


def test_every_translate_reason_is_declared() -> None:
    """The vocabulary is data, so a new code cannot reach a row unannounced."""
    assert TRANSLATE_DRIFT_REASONS
    assert len(set(TRANSLATE_DRIFT_REASONS)) == len(TRANSLATE_DRIFT_REASONS)
    assert "not_translated" in TRANSLATE_DRIFT_REASONS
    assert "language_flip" not in TRANSLATE_DRIFT_REASONS
