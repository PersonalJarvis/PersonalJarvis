"""Visual-intent classification: the three-valued verdict.

The cases below are the behaviour contract, not a sample. Two of them are
regressions waiting to happen and are pinned deliberately:

* a look-verb inside an idiom ("looks like", "mal sehen", "a ver si") must not
  capture — this is the failure mode ``jarvis/brain/cu_gate.py`` hit in
  production with product names, and the same masking approach is used here;
* a plain content question must produce neither a capture nor a question,
  because a feature that interrupts to ask "shall I look?" on every third turn
  is worse than one that never looks at all.
"""
from __future__ import annotations

import pytest

from jarvis.screen_context.intent import (
    SUPPORTED_CLARIFY_LOCALES,
    clarifying_question,
    classify,
)
from jarvis.screen_context.models import VisualIntent


@pytest.mark.parametrize(
    "utterance",
    [
        # -- English
        "can you see this?",
        "take a look at this",
        "look at the error",
        "what does it say?",
        "read this out to me",
        "what's on my screen?",
        "check this out",
        "take a screenshot",
        # -- German (the two phrasings the feature is specified around)
        "Kannst du mal sehen?",
        "Schau dir das an",
        "schau mal",
        "guck dir das mal an",
        "siehst du das?",
        "was steht da?",
        "lies mir das vor",
        "auf dem Bildschirm ist eine Fehlermeldung",  # i18n-allow: DE input
        "mach einen Screenshot",  # i18n-allow: DE input
        # -- Spanish
        "mira esto",
        "puedes ver esto?",
        "que dice ahi?",
        "echa un vistazo",
        "haz una captura de pantalla",
    ],
)
def test_unambiguous_requests_capture(utterance: str) -> None:
    verdict = classify(utterance)
    assert verdict.wants_capture, f"{utterance!r} -> {verdict.intent}"
    assert verdict.evidence, "an unambiguous verdict must record its evidence"


@pytest.mark.parametrize(
    "utterance",
    [
        "look at this window",
        "what does it say in this dialog?",
        "can you see this tab?",
        "schau dir dieses Fenster an",
        "was steht da in diesem Dialog?",
        "mira esta ventana",
    ],
)
def test_window_scope_is_detected(utterance: str) -> None:
    assert classify(utterance).intent is VisualIntent.WINDOW


@pytest.mark.parametrize(
    "utterance",
    [
        "what is that?",
        "why is this?",
        "can you check that?",
        "was ist das?",  # i18n-allow: DE input
        "warum ist das?",  # i18n-allow: DE input
        "kannst du das mal prüfen?",  # i18n-allow: DE input
        "que es esto?",
        "puedes revisar?",
    ],
)
def test_weak_signals_ask_instead_of_capturing(utterance: str) -> None:
    verdict = classify(utterance)
    assert verdict.intent is VisualIntent.AMBIGUOUS, f"{utterance!r}"
    assert verdict.needs_clarification
    assert not verdict.wants_capture, "an ambiguous turn must NEVER capture"


@pytest.mark.parametrize(
    "utterance",
    [
        # Plain content questions — the overwhelmingly common case.
        "what did we just talk about?",
        "was haben wir besprochen?",
        "explain recursion to me again",
        "wie spät ist es?",  # i18n-allow: DE input
        "remind me to call the dentist tomorrow",
        # Idioms that merely CONTAIN a look/see verb.
        "that looks like a good plan",
        "let's see what happens",
        "I see, that makes sense",
        "can you look into the billing issue?",
        "look for a cheaper flight",
        "mal sehen was daraus wird",  # i18n-allow: DE input
        "das sieht gut aus",  # i18n-allow: DE input
        "schauen wir mal",
        "ya veo, gracias",
        "vamos a ver que pasa",
    ],
)
def test_non_visual_turns_are_left_alone(utterance: str) -> None:
    verdict = classify(utterance)
    assert verdict.intent is VisualIntent.NONE, f"{utterance!r} -> {verdict.intent}"


def test_idiom_masking_does_not_veto_a_real_request() -> None:
    """An idiom and a real request in one sentence must still capture.

    Masking rather than vetoing is exactly what makes this work; a veto would
    drop the request because the sentence also contains "looks like".
    """
    verdict = classify("that looks like an error — can you see this?")
    assert verdict.wants_capture


def test_empty_turn_is_never_a_request() -> None:
    """Non-conversational callers reach the service without an utterance."""
    assert classify("").intent is VisualIntent.NONE
    assert classify("   ").intent is VisualIntent.NONE


def test_umlauts_and_accents_match_without_diacritics() -> None:
    umlaut = classify("kannst du das mal prüfen?")  # i18n-allow: DE input
    assert umlaut.intent is VisualIntent.AMBIGUOUS
    assert classify("mira esta pestaña").intent is VisualIntent.WINDOW


@pytest.mark.parametrize("locale", sorted(SUPPORTED_CLARIFY_LOCALES))
def test_every_supported_locale_has_a_clarifying_question(locale: str) -> None:
    """§1.3: a phrase table carries ALL supported languages, never de/en only."""
    question = clarifying_question(locale)
    assert question and question.strip().endswith("?")


def test_clarifying_question_falls_back_for_an_unknown_locale() -> None:
    """A locale with no entry gets the default, never an empty string."""
    assert clarifying_question("fr") == clarifying_question("en")
    assert clarifying_question("") == clarifying_question("en")


def test_clarifying_question_accepts_a_full_bcp47_tag() -> None:
    assert clarifying_question("de-DE") == clarifying_question("de")
    assert clarifying_question("es_ES") == clarifying_question("es")
