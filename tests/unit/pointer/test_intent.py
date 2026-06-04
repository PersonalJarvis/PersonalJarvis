"""Tests for the AI-Pointer deictic intent gate.

The gate decides whether an utterance deictically refers to the on-screen
element under the cursor ("was ist das da?", "what is this?") and must NOT
fire for utterances merely containing a demonstrative completed by a concrete
noun ("was ist das fuer ein Wetter?"). See docs/plans/ai-pointer/DESIGN.md sec 5.
"""

from __future__ import annotations

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


def test_strong_phrase_beats_veto() -> None:
    # A genuine pointing question can still contain a "das fuer ein" fragment.
    text = "Was ist das fuer ein schoenes Bild, worauf ich zeige?"
    assert is_pointing_intent(text) is True
