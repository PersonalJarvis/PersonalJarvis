"""Adversarially-verified deictic-gate battery (AI Pointer, 2026-06-02).

73 cases produced by the ai-pointer-deep-dive-fix workflow's adversarial skeptic
phase: natural pointing phrasings MUST fire ("Was siehst du hier?", "Was steht
da?", "Lies das"); whole-screen vision ("was siehst du", "was geht ab"), the
weather veto, and verb+concrete-noun ("zeig mir das wetter", "show me that
report") MUST NOT. One documented benign residual FP is excluded here; the gate
broadening is the fix for the live "described the whole screen / I lack the tool"
voice bug. See docs/plans/ai-pointer/DESIGN.md.
"""

from __future__ import annotations

import pytest

from jarvis.pointer.intent import is_pointing_intent

_FIRE = [
    "Was siehst du hier?",
    "Was siehst du da?",
    "Was siehst du dort?",
    "Was steht da?",
    "Was steht hier?",
    "Lies mir das vor",
    "Lies das",
    "read me that",
    "read that aloud",
    "read this",
    "Welches Wort ist das?",
    "Welches Wort da?",
    "which word is that",
    "which word is this",
    "was ist dieses wort da",
    "Was ist das Wort hier?",
    "what is written here",
    "what's written there",
    "Zeige mir das",
    "Zeig mir das",
    "show me that",
    "was ist das da",
    "was ist das hier",
    "what do you see here",
    "what do you see there",
    "siehst du hier",
    "siehst du da",
    "zeig mir das dokument hier",
    "lies mir den text hier vor",
    "siehst du den fehler hier",
    "kannst du sehen was da steht",
]

_NO_FIRE = [
    "was siehst du",
    "was liest du",
    "was ist auf dem bildschirm",
    "was ist sichtbar",
    "what is on the screen",
    "what can you see",
    "can you see anything",
    "is there anything visible",
    "what can you describe",
    "was hast du gesehen",
    "was zeigt sich",
    "was ist erkennbar",
    "what do you observe",
    "what's visible now",
    "was siehst du gerade auf dem bildschirm",
    "what do you see on the screen",
    "was ist das fuer ein wetter",
    "was ist das fuer eine frage",
    "was ist das wetter heute",
    "what is the weather",
    "zeig mir das wetter",
    "lies mir das wetter vor",
    "show me the weather",
    "zeig mir das menue",
    "zeig mir das dokument",
    "show me that report",
    "do you see that bug",
    "siehst du das problem",
    "ich sehe das nicht",
    "read me the news",
    "was ist da",
    "was geht ab",
    "was ist los",
    "what's happening",
    "was ist eine flasche",
    "",
    "   ",
]


@pytest.mark.parametrize("phrase", _FIRE)
def test_battery_must_fire(phrase: str) -> None:
    assert is_pointing_intent(phrase) is True, f"{phrase!r} should fire the pointer gate"


@pytest.mark.parametrize("phrase", _NO_FIRE)
def test_battery_must_not_fire(phrase: str) -> None:
    assert is_pointing_intent(phrase) is False, f"{phrase!r} must NOT fire the pointer gate"
