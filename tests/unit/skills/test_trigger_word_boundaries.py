"""Built-in skill triggers must match WORDS, not substrings.

Found while tracing BUG-121 (voice session 2026-07-29 17:04). The flight
recorder showed the Google Calendar skill firing at band=fire, score 1.0, on a
sentence about the coding workspace. The reason was one missing pair of word
boundaries: the pattern's ``termin`` alternative matched inside "coding
TERMINals". A sweep found the same defect in three more built-ins, one of them
worse than the original:

* ``plugin-stripe`` — ``kunde`` fires inside "SeKUNDE", so every spoken "warte
  eine Sekunde" pulled a PAYMENT skill into the turn.
* ``plugin-github`` — ``repo`` fires inside "REPOrt" and "REPOrter".
* ``plugin-todoist`` — a bare ``to-?do`` matches Spanish "todo" / "todos"
  ("everything" / "all"), so ordinary Spanish speech pulled in a task skill.

A skill that fires wrongly is not cosmetic: it is injected into the turn's
instructions and it declares its tools, which is how an unrelated sentence ends
up next to a payment API. This file is the class guard — it reads the SHIPPED
frontmatter, so a future skill that reintroduces a bare substring fails here
rather than in a live call.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

_BUILTIN = Path(__file__).resolve().parents[3] / "jarvis" / "skills" / "builtin"

# Everyday speech that must reach NO plugin skill. Each line is either a real
# transcript from the failing session or the minimal sentence that reproduces
# one of the substring hits found in the sweep.
INNOCENT = [
    # BUG-121 itself: the workspace vocabulary the user says constantly.
    "Kannst du mal bitte Terminal T7 prompten",  # i18n-allow: speech input under test
    "die Coding Terminals sind alle offen",  # i18n-allow: speech input under test
    "close the terminal",
    # "Sekunde" / "Urkunde" carry "kunde".
    "warte mal eine Sekunde",  # i18n-allow: speech input under test
    "gib mir noch eine halbe Sekunde",  # i18n-allow: speech input under test
    "in der Urkunde steht das Datum",  # i18n-allow: speech input under test
    # "Report" / "Reporter" carry "repo".
    "mach mir einen Report",  # i18n-allow: speech input under test
    "der Reporter hat angerufen",  # i18n-allow: speech input under test
    # Spanish "todo" / "todos" are ordinary words.
    "todos los terminales están abiertos",  # i18n-allow: speech input under test
    "todo bien, gracias",  # i18n-allow: speech input under test
    # The English infinitive "to do" is ordinary grammar, not a task list.
    # Both sentences are the live 2026-08-06 transcripts that pulled the
    # todoist skill into an Agentic-IDE turn at score 1.0.
    (
        "Can you please prompt terminal T1 to do a deep dive and find out "
        "about bugs related with the new codex voice feature?"
    ),
    (
        "Could you please prompt terminal t1 to do a deep dive and look for "
        "bugs related on macOS?"
    ),
    "what do you want me to do",
]

# The recall side, per skill: what must still fire after the boundaries land.
# Without these the guard above could be satisfied by deleting the triggers.
STILL_FIRES: list[tuple[str, str]] = [
    ("plugin-google_calendar", "zeig mir meine Termine"),  # i18n-allow: speech input under test
    ("plugin-google_calendar", "an welchen Terminen bin ich frei"),  # i18n-allow: speech input under test
    ("plugin-google_calendar", "was steht im Kalender"),  # i18n-allow: speech input under test
    ("plugin-google_calendar", "check my google calendar"),
    ("plugin-stripe", "zeig die offenen Rechnungen"),  # i18n-allow: speech input under test
    ("plugin-stripe", "wie viele Kunden habe ich"),  # i18n-allow: speech input under test
    ("plugin-github", "öffne den pull request"),  # i18n-allow: speech input under test
    ("plugin-github", "welche repos habe ich"),  # i18n-allow: speech input under test
    ("plugin-todoist", "setz das auf meine To-do-Liste"),  # i18n-allow: speech input under test
    ("plugin-todoist", "add it to my todo list"),
    ("plugin-todoist", "put milk on my to do list"),
    ("plugin-todoist", "check my to-dos"),
    ("plugin-todoist", "what is on my to dos for today"),
    ("plugin-todoist", "meine Einkaufsliste"),  # i18n-allow: speech input under test
]


def _voice_patterns() -> dict[str, list[str]]:
    """Every built-in skill's shipped voice-trigger patterns, by skill name."""
    out: dict[str, list[str]] = {}
    for skill_md in sorted(_BUILTIN.glob("*/SKILL.md")):
        text = skill_md.read_text(encoding="utf-8")
        parts = text.split("---")
        if len(parts) < 3:
            continue
        try:
            data = yaml.safe_load(parts[1]) or {}
        except yaml.YAMLError:  # a malformed skill is another test's problem
            continue
        patterns = [
            str(trigger.get("pattern") or "")
            for trigger in (data.get("triggers") or [])
            if isinstance(trigger, dict) and trigger.get("type") == "voice"
        ]
        if patterns:
            out[skill_md.parent.name] = patterns
    return out


def test_the_builtin_skills_are_readable() -> None:
    """Guard the guard: an empty sweep would make every test below vacuous."""
    patterns = _voice_patterns()
    assert len(patterns) > 10
    assert "plugin-google_calendar" in patterns


@pytest.mark.parametrize("utterance", INNOCENT)
def test_no_plugin_skill_fires_on_ordinary_speech(utterance: str) -> None:
    fired = [
        name
        for name, patterns in _voice_patterns().items()
        if any(re.search(pattern, utterance, re.I) for pattern in patterns)
    ]

    assert fired == [], f"{utterance!r} wrongly triggered {fired}"


@pytest.mark.parametrize("skill,utterance", STILL_FIRES)
def test_the_real_phrasings_still_fire(skill: str, utterance: str) -> None:
    patterns = _voice_patterns().get(skill) or []

    assert patterns, f"{skill} has no voice trigger"
    assert any(re.search(pattern, utterance, re.I) for pattern in patterns)
