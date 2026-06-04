"""Tests for the visual-reference vision gate (Hybrid — attach-only-on-reference).

Contract (inverted from the old skip-when-safe default): a screenshot is
attached ONLY when the utterance clearly refers to the screen (deictic pointer,
screen noun, look/click verb, read-out/diagnosis). A plain content question —
even a non-smalltalk one — gets NO screenshot, so the conversation history stays
the model's primary context. The on-demand screenshot tool (Wave 2) is the
safety net for references the markers miss, replacing the old conservative
attach-on-every-non-smalltalk stance that buried the conversation under the
current screen.
"""
from __future__ import annotations

import pytest

from jarvis.brain.vision_gate import has_visual_marker, should_attach_screenshot

# Turns that clearly refer to the screen -> attach.
_VISUAL = [
    "was siehst du hier",
    "schau mal das hier",
    "klick auf den Button",
    "warum ist das rot?",
    "lies mir die Fehlermeldung vor",
    "was steht da auf dem Bildschirm",
    "mach das Fenster zu",
    "was ist das da",
    "klick das weg",
    "look at this window",
    # live 2026-05-31 failures: spatial + read-out references the old list missed
    "vor was genau da steht da oben links",
    "liest es mir vor",
    "was steht oben links",
    "lies mir vor was da unten steht",
]

# Turns that are conversational / factual / a plain action -> no screenshot,
# even though they are not "smalltalk". This is the user's reported case.
_NON_VISUAL = [
    "was haben wir gerade besprochen?",
    "erklär mir nochmal das Thema",
    "erklär mir was ein vektor ist",
    "wie spät ist es",
    "was ist die Hauptstadt von Frankreich",
    "warum ist das so wichtig?",  # "warum ist das" is NOT a marker without a colour
    "fass das bitte zusammen",
    "hallo jarvis",
    "danke dir",
    "wir hatten einen langen Dialog darüber",  # "dialog" alone must NOT fire (DE = conversation)
    "was steht heute an?",  # "steht an" = scheduled, NOT a screen read-out
]


@pytest.mark.parametrize("text", _VISUAL)
def test_visual_reference_attaches(text: str) -> None:
    # A visual reference attaches regardless of the smalltalk classification.
    assert should_attach_screenshot(text, is_smalltalk=False) is True
    assert should_attach_screenshot(text, is_smalltalk=True) is True


@pytest.mark.parametrize("text", _NON_VISUAL)
def test_non_visual_skips(text: str) -> None:
    # Inverted logic: a non-smalltalk content question no longer auto-attaches.
    assert should_attach_screenshot(text, is_smalltalk=False) is False
    assert should_attach_screenshot(text, is_smalltalk=True) is False


def test_visual_marker_detection_is_case_insensitive() -> None:
    assert has_visual_marker("Was ist DAS HIER") is True
    assert has_visual_marker("look at this") is True
    assert has_visual_marker("auf dem Bildschirm") is True


def test_plain_text_has_no_marker() -> None:
    assert has_visual_marker("wie geht es dir") is False
    assert has_visual_marker("guten morgen") is False
    assert has_visual_marker("was haben wir besprochen") is False


def test_has_visual_marker_examples() -> None:
    assert has_visual_marker("klick drauf") is True
    assert has_visual_marker("erklär mir das Thema") is False
