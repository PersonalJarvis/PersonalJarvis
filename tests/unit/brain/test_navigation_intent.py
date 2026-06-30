"""Tests for ``match_navigation_intent`` — the deterministic UI-navigation gate.

A clear "go to section X" command must resolve to a canonical section id so the
brain can move the UI deterministically (before the capability gate, which would
otherwise refuse "zeig die Socials" because 'social' is an external-integration
marker, and before the force-spawn heuristic). It must be conservative: a
navigation cue AND a known section are both required, so unrelated utterances
never hijack the UI. Pure regex, no LLM (AP-9/AP-11).
"""
from __future__ import annotations

import pytest

from jarvis.brain.navigation_intent import match_navigation_intent


@pytest.mark.parametrize(
    "text,expected",
    [
        ("zeig die Socials", "socials"),
        ("zeige mir die Socials", "socials"),
        ("öffne die Einstellungen", "settings"),
        ("geh zu den Aufgaben", "tasks"),
        ("wechsel zu den Notizen", "memory"),
        ("show the agents", "agents"),
        ("open settings", "settings"),
        ("go to the board", "board"),
        ("navigate to socials", "socials"),
        ("zeig mir die sub-agents", "agents"),
    ],
)
def test_positive_navigation(text: str, expected: str) -> None:
    assert match_navigation_intent(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        "wie ist das Wetter",
        "was kann ich in den Einstellungen einstellen",  # mentions settings, no nav cue
        "spiel Musik",
        "lösche die Aufgabe drei",  # 'lösche' is not a navigation cue
        "öffne WhatsApp",  # nav cue but no known section
        "erzähl mir einen Witz",
    ],
)
def test_negative_navigation(text: str) -> None:
    assert match_navigation_intent(text) is None


def test_keyboard_does_not_match_board() -> None:
    """Word boundaries: 'keyboard' must not match the 'board' section."""
    assert match_navigation_intent("öffne das keyboard") is None
