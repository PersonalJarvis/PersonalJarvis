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


# The utterance from the live failure (voice session 2026-07-29 17:04, BUG-121),
# verbatim off the flight recorder. The cue "open" is the CLI product name "Open
# Code"; the section word "sessions" is part of a bug description 30 characters
# later. Under the pre-fix matcher this returned "sessions", the navigation fast
# path claimed the turn, and terminal T7 — which the sentence explicitly
# addresses — was never briefed.
_BUG_121_UTTERANCE = (
    "Kannst du mal bitte Terminal T7 prompten, dass es einen Deep Dive machen, "
    "sondern analysieren soll, wieso das Resuming Feature von ähm unseren "
    "Agentic ID, was wir eingebaut haben, nur bei ähm claude Code Sessions "
    "funktioniert und nicht z.B. bei Codec Sessions oder bei Open Codes oder "
    "bei anderen Sessions. Also, ich möchte, dass das Resuming Feature bei "
    "allen ähm Coding Terminals, welche wir verbunden haben, funktioniert."
)


def test_bug_121_live_utterance_does_not_navigate() -> None:
    """A cue and a section word from unrelated clauses must not navigate."""
    assert match_navigation_intent(_BUG_121_UTTERANCE) is None


@pytest.mark.parametrize(
    "text",
    [
        # Cue and section in different clauses, joined by a conjunction.
        "bei Open Codes oder bei anderen Sessions",
        "open the file and then the sessions broke",
        # Cue and section separated by more than a command's worth of words.
        "open a terminal, wait for the banner, then look at the sessions",
        # Section word BEFORE the cue is never a target.
        "die Sessions sind kaputt, kannst du das aufmachen",
        # Cue and section in different sentences.
        "mach das Terminal auf. Die Sessions laufen nicht.",
        # A bug report that mentions both, which is the shape that broke live.
        "warum funktioniert open code nicht mit den sessions",
    ],
)
def test_unbound_cue_and_section_do_not_navigate(text: str) -> None:
    assert match_navigation_intent(text) is None


@pytest.mark.parametrize(
    "text,expected",
    [
        # An article and a filler between verb and target still binds.
        ("zeig mir doch bitte die Einstellungen", "settings"),
        ("geh zu den offenen Aufgaben", "tasks"),
        ("open the settings", "settings"),
    ],
)
def test_binding_tolerates_ordinary_filler(text: str, expected: str) -> None:
    """The proximity rule must not break how people actually phrase commands."""
    assert match_navigation_intent(text) == expected
