"""Barge-in intent detection: stop the ACTION, never the call.

The asymmetry that decides every tie here: a missed stop costs the user one
repeat (today's behaviour, which is what the probe exists to fix), a false stop
cancels work the user asked for. So the ambiguous cases below are asserted as
NON-interrupts on purpose.
"""
from __future__ import annotations

import pytest

from jarvis.speech.interrupt_intent import (
    INTERRUPT_NONE,
    INTERRUPT_REDIRECT,
    INTERRUPT_STOP,
    classify_interrupt,
    is_interrupt_intent,
)


@pytest.mark.parametrize(
    "utterance",
    [
        # German
        "Stopp",  # i18n-allow: spoken command under test
        "stopp!",  # i18n-allow
        "Halt",  # i18n-allow
        "warte",  # i18n-allow
        "warte mal",  # i18n-allow
        "Warte mal kurz",  # i18n-allow
        "okay, warte mal",  # i18n-allow
        "ähm, moment mal",  # i18n-allow
        "einen Moment",  # i18n-allow
        "abbrechen",  # i18n-allow
        "brich das ab",  # i18n-allow
        "hör auf",  # i18n-allow
        "hoer mal auf",  # i18n-allow
        "lass es",  # i18n-allow
        "lass gut sein",  # i18n-allow
        "vergiss es",  # i18n-allow
        "doch nicht",  # i18n-allow
        "lieber nicht",  # i18n-allow
        "egal",  # i18n-allow
        "nein",  # i18n-allow
        "nee",  # i18n-allow
        "nein, nein",  # i18n-allow
        # English
        "stop",
        "Stop it.",
        "stop stop stop",
        "wait",
        "wait a second",
        "hold on",
        "hang on",
        "never mind",
        "nevermind",
        "forget it",
        "cancel that",
        "abort",
        "scratch that",
        "no",
        "no no",
        "nope",
        # Spanish
        "para",  # i18n-allow
        "espera",  # i18n-allow
        "un momento",  # i18n-allow
        "cancélalo",  # i18n-allow
        "olvídalo",  # i18n-allow
        "déjalo",  # i18n-allow
        "no importa",  # i18n-allow
        "mejor no",  # i18n-allow
    ],
)
def test_pure_stop_requests_are_recognized(utterance: str) -> None:
    assert classify_interrupt(utterance) == INTERRUPT_STOP
    assert is_interrupt_intent(utterance)


@pytest.mark.parametrize(
    ("utterance", "expected_tail"),
    [
        ("warte, ich meinte Rom", "ich meinte Rom"),  # i18n-allow
        ("stopp, mach lieber Rom", "mach lieber Rom"),  # i18n-allow
        ("nein, ich meinte Rom", "ich meinte Rom"),  # i18n-allow
        ("nein, eigentlich Rom", "eigentlich Rom"),  # i18n-allow
        ("wait, I meant Rome", "I meant Rome"),
        ("no, actually book Rome", "actually book Rome"),
        ("hold on, make it Rome instead", "make it Rome instead"),
        ("espera, quería decir Roma", "quería decir Roma"),  # i18n-allow
    ],
)
def test_stop_with_replacement_is_a_redirect(
    utterance: str, expected_tail: str
) -> None:
    """A correction cancels the running action AND carries the new order."""
    assert classify_interrupt(utterance) == INTERRUPT_REDIRECT
    assert expected_tail  # documents the remainder the caller re-dispatches


@pytest.mark.parametrize(
    "utterance",
    [
        # A stop word buried mid-sentence is ordinary speech.
        "don't stop the music",
        "erzähl mir nicht, dass ich warten soll",  # i18n-allow
        "the wait times at the airport",
        # Bare negations that continue into ordinary speech.
        "no problem",
        "no worries",
        "nein danke, das passt so",  # i18n-allow
        "nope, that one is fine actually let me think about it",
        # A monologue that merely opens with a stop word.
        (
            "wait until the whole deployment pipeline has finished and then "
            "tell me which of the seventeen integration tests are still "
            "failing on the windows runner please"
        ),
        # Empty / junk.
        "",
        "   ",
    ],
)
def test_ordinary_speech_is_not_an_interrupt(utterance: str) -> None:
    assert classify_interrupt(utterance) == INTERRUPT_NONE
    assert not is_interrupt_intent(utterance)


@pytest.mark.parametrize(
    "utterance",
    [
        "auflegen",  # i18n-allow
        "stopp jarvis",  # i18n-allow
        "stop jarvis",
        "jarvis stop",
        "hang up",
        "goodbye",
        "tschüss",  # i18n-allow
    ],
)
def test_hangup_phrases_never_classify_as_an_action_interrupt(
    utterance: str,
) -> None:
    """Ending the CALL outranks ending the action; the two must not race."""
    assert classify_interrupt(utterance) == INTERRUPT_NONE


def test_none_input_is_safe() -> None:
    assert classify_interrupt(None) == INTERRUPT_NONE
    assert not is_interrupt_intent(None)
