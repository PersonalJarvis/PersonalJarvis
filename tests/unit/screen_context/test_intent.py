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
    requests_screen_operation,
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
        "look at my screen",
        "analyze the screenshot",
        "what can you see on my monitor?",
        "inspect the current window",
        # -- German (the two phrasings the feature is specified around)
        "Kannst du mal sehen?",
        "Kannst du mal schauen?",
        "Schau dir das an",
        "Schau dir das mal an",
        "schau mal",
        "guck dir das mal an",
        "siehst du das?",
        "was steht da?",
        "lies mir das vor",
        "auf dem Bildschirm ist eine Fehlermeldung",  # i18n-allow: DE input
        "Wenn sowas auf meinem Bildschirm ist, was bedeutet das?",  # i18n-allow: DE input
        "mach einen Screenshot",  # i18n-allow: DE input
        "Schau dir bitte meinen Bildschirm an",  # i18n-allow: DE input
        "Pruef bitte, was auf meinem Monitor steht",  # i18n-allow: DE input
        "Lies bitte dieses Fenster",  # i18n-allow: DE input
        "Kannst du meinen Bildschirm anschauen?",  # i18n-allow: DE input
        "Was steht in diesem Fenster?",  # i18n-allow: DE input
        # Spoken German uses the anglicisms freely; the miss sent a live turn
        # into a blind tool loop instead of the one-shot look (voice session
        # 2026-08-06 20:51). i18n-allow: German speech-input fixtures
        (
            "Hallo, hallo, ich wollte mal wissen, irgendwas, "  # i18n-allow
            "was du auf meinen Screen siehst."  # i18n-allow: DE input
        ),
        "Was ist auf meinem Display?",  # i18n-allow: DE input
        "Was siehst du auf meinem Screen?",  # i18n-allow: DE input
        # -- Spanish
        "mira esto",
        "puedes ver esto?",
        "que dice ahi?",
        "echa un vistazo",
        "haz una captura de pantalla",
        "analiza esta captura de pantalla",
        "que ves en mi monitor",
    ],
)
def test_unambiguous_requests_capture(utterance: str) -> None:
    verdict = classify(utterance)
    assert verdict.wants_capture, f"{utterance!r} -> {verdict.intent}"
    assert verdict.evidence, "an unambiguous verdict must record its evidence"


@pytest.mark.parametrize(
    "utterance",
    [
        "click the button on my screen",
        "look at this and then scroll down",
        "use Computer-Use and take a screenshot",
        "submit this form on my screen",
        "zoom in on my screen",
        "save this document on my screen",
        "delete this file on my screen",
        "Refresh this page",
        "Log in",
        "Would you mind clicking the button?",
        "Klick den Knopf auf meinem Bildschirm",  # i18n-allow: DE input
        "Kannst du den Button auf meinem Bildschirm anklicken?",  # i18n-allow: DE input
        "Bitte den Knopf auf meinem Bildschirm anklicken",  # i18n-allow: DE input
        "Schau dir das an und scrolle dann nach unten",  # i18n-allow: DE input
        "pulsa el botón en mi pantalla",
        "Explain this, then close the window.",
    ],
)
def test_desktop_operations_belong_to_computer_use(utterance: str) -> None:
    assert requests_screen_operation(utterance)
    assert classify(utterance).intent is VisualIntent.NONE


@pytest.mark.parametrize(
    "utterance",
    [
        "which button should I click on my screen?",
        "Welchen Knopf soll ich auf meinem Bildschirm anklicken?",  # i18n-allow: DE input
        "¿qué botón debo pulsar en mi pantalla?",
    ],
)
def test_operation_advice_remains_a_read_only_look(utterance: str) -> None:
    assert not requests_screen_operation(utterance)
    assert classify(utterance).wants_capture


@pytest.mark.parametrize(
    "utterance",
    [
        "I clicked the button and now this error is on my screen",
        "can you explain which button to click on my screen?",
        "Save is greyed out on my screen",
        # i18n-allow: German speech-input fixtures
        (
            "Ich habe den Knopf angeklickt und jetzt ist der Fehler "  # i18n-allow
            "auf meinem Bildschirm"  # i18n-allow
        ),
        "Kannst du erklären, welchen Knopf ich auf meinem Bildschirm anklicken soll?",  # i18n-allow
        "Pulsé el botón y ahora aparece este error en mi pantalla",
    ],
)
def test_past_actions_and_action_advice_never_drive_the_desktop(utterance: str) -> None:
    assert not requests_screen_operation(utterance)
    assert classify(utterance).wants_capture


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
        "Was ist das denn?",  # i18n-allow: DE input
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
        # A deictic followed by a content word is a DETERMINER: the sentence
        # is about that word, not the screen. "Was ist das  # i18n-allow: quoted DE input
        # Beliebteste?" continued a boxing conversation and  # i18n-allow: quoted DE input
        # Jarvis derailed it twice with its clarifying question
        # (voice session 2026-08-06 18:33).
        "Was ist das Beliebteste?",  # i18n-allow: DE input — the live nag
        "Was waren die zehn besten Boxer der Geschichte?",  # i18n-allow
        "Warum ist das Wetter heute so schlecht?",  # i18n-allow: DE input
        "Stimmt das Gerücht über die neuen Preise?",  # i18n-allow: DE input
        "Was bedeutet das Wort Serendipität?",  # i18n-allow: DE input
        "Was kostet das Programm?",  # i18n-allow: DE input
        "Das Menü im Restaurant war fantastisch",  # i18n-allow: DE input
        "what is that movie about?",
        "why is that happening so often?",
        "what about that restaurant we discussed?",
        # A look-verb or check-verb WITH a content object is a lookup/research
        # request — the screen answers nothing about it.
        "Schau mal, was kosten die Flüge nach Rom?",  # i18n-allow: DE input
        "Kannst du mal schauen, ob es morgen regnet?",  # i18n-allow: DE input
        "Kannst du mir die Nachrichten vorlesen?",  # i18n-allow: DE input
        "Lies mir das Buch vor",  # i18n-allow: DE input
        "can you check the weather for tomorrow?",
        "have a look at the flight prices",
        "take a look at the hotel reviews",
        "what does the contract say about cancellation?",
        "read the news to me",
        "did you get that message from Anna?",
        "puedes revisar el precio del bitcoin?",
        "échale un vistazo a los precios de los vuelos",
        "mira esta receta de pasta",
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


@pytest.mark.parametrize(
    "utterance",
    [
        "How can you look at my screen?",
        "How do I let you take a screenshot?",
        "What happens when I ask for a screenshot?",
        "Wie kannst du meinen Bildschirm sehen?",  # i18n-allow: DE input
        "Wie kann ich dir meinen Bildschirm zeigen?",  # i18n-allow: DE input
        "Was passiert, wenn ich Screenshot sage?",  # i18n-allow: DE input
        "Como puedes ver mi pantalla?",  # i18n-allow: ES input
    ],
)
def test_product_questions_are_not_capture_consent(utterance: str) -> None:
    assert classify(utterance).intent is VisualIntent.NONE


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
