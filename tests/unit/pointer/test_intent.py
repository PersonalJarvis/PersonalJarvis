"""Tests for the AI-Pointer deictic intent gate.

The gate decides whether an utterance deictically refers to the on-screen
element under the cursor ("was ist das da?", "what is this?") and must NOT
fire for utterances merely containing a demonstrative completed by a concrete
noun ("was ist das fuer ein Wetter?"). See docs/plans/ai-pointer/DESIGN.md sec 5.
"""

from __future__ import annotations

from time import perf_counter

import pytest

from jarvis.pointer.intent import is_pointing_intent


@pytest.mark.parametrize(
    "text",
    [
        # German deictic, locative-anchored
        "Was ist das da?",
        "Was ist das hier?",
        "Was ist das dort drueben?",
        "Erklaer mir das hier mal",
        "Was ist dieses Ding hier?",
        # German pointing-verb / cursor reference
        "Worauf zeige ich gerade?",
        "Wo ich hinzeige, was ist das?",
        "Was ist da unter meinem Cursor?",
        # Bare demonstrative question (no trailing noun)
        "Was ist das?",
        # English deictic
        "What is this?",
        "What's this thing?",
        "Explain this, right here",
        "What am I pointing at?",
        "What is written here?",
        "What is written\nhere?",
        "Was steht\nhier?",
        "Kannst du lesen,\nwas hier steht?",
        "What did I write here?",
        "Who wrote the text here?",
        "Please pause. What is written here?",
        "Read the label here, please.",
    ],
)
def test_fires_on_deictic_pointing(text: str) -> None:
    assert is_pointing_intent(text) is True


@pytest.mark.parametrize(
    "text",
    [
        # The user's canonical counter-example: demonstrative completed by a noun
        "Was ist das fuer ein Wetter?",
        "Was ist das fuer ein Auto?",
        # Demonstrative completed by a concrete noun, no pointing
        "Was ist das Wetter heute?",
        "Wie ist das Wetter?",
        # Plain non-deictic requests
        "Erzaehl mir einen Witz",
        "Wie spaet ist es?",
        "Starte den Browser",
        "Wie geht es dir?",
        "Schreibe eine Mail an Tom",
        # Empty / whitespace
        "",
        "   ",
    ],
)
def test_does_not_fire_on_non_deictic(text: str) -> None:
    assert is_pointing_intent(text) is False


@pytest.mark.parametrize(
    "text",
    [
        "Write an internal announcement. Return the draft text here for human review.",
        "Write an internal announcement here for human review.",
        "She writes an internal announcement here for human review.",
        "I wrote an internal announcement here for human review.",
        (
            "[assignment from the user]\n"
            "Prepare a communication draft for review. Do not send or publish it.\n\n"
            "Write an internal announcement. Return the draft text here for human review."
        ),
        "Lies den Bericht. Antworte hier im Chat.",
    ],
)
def test_composition_and_reply_location_are_not_pointing(text: str) -> None:
    assert is_pointing_intent(text) is False


@pytest.mark.parametrize("separator", [". ", "! ", "? ", "; ", "\n", "\r\n"])
@pytest.mark.parametrize("lead", ["Read the report", "What did I write"])
def test_weak_visual_reference_does_not_borrow_another_sentences_location(
    separator: str, lead: str
) -> None:
    text = f"{lead}{separator}Return the summary here for review."
    assert is_pointing_intent(text) is False


def test_long_whitespace_without_a_location_does_not_stall_the_gate() -> None:
    # Overlapping optional whitespace runs previously took many seconds here.
    text = "read" + " " * 32768 + "end"
    started = perf_counter()
    assert is_pointing_intent(text) is False
    assert perf_counter() - started < 2.0


def test_strong_phrase_beats_veto() -> None:
    # A genuine pointing question can still contain a "das fuer ein" fragment.
    text = "Was ist das fuer ein schoenes Bild, worauf ich zeige?"
    assert is_pointing_intent(text) is True
