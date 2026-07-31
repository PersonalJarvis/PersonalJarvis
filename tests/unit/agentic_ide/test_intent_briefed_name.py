"""A garbled call-sign is repaired where the sentence vouches for it.

Maintainer report 2026-07-28: "he sometimes prompts the wrong terminal". The
measurable half of that is worse than wrong — it is NOTHING. "Hey, prompte adex"
(the maintainer's own example) addressed no pane at all: "adex" reaches "Alex"
only on the fuzzy band, so the canonicaliser left it alone, every addressing
template is anchored on the exact spelling, and the last-resort branch rejects a
name that is not certain. Three layers, one silent miss.

Fuzzy matching cannot simply be switched on to fix that. Measured against the
shipping name pool, ordinary words of the spoken language reach it — "unten"
scores into "Hunter", "keine" into "Kai" — and a sentence rewritten on that
evidence briefs a pane nobody named.

What makes the difference is POSITION. Directly behind a briefing verb there is
exactly one thing a word can be: the addressee. So the fuzzy band is admitted in
that slot and nowhere else. These tests pin both halves — the repair, and the
ordinary sentences that must stay untouched.
"""
from __future__ import annotations

import pytest

from jarvis.agentic_ide.intent import detect_all

PANES = ["Alex", "Ellis", "Casey", "Dana"]


def _addressed(text: str) -> list[str]:
    return [found.terminal for found in detect_all(text, names=PANES)]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # The live example, and its English twin.
        ("Hey, prompte adex mal die Wake-Pipeline zu prüfen", "Alex"),  # i18n-allow: input vocab
        ("prompt adex to review the wake path", "Alex"),
        # A pane noun between the verb and the name changes nothing.
        ("prompt the terminal adex to fix the tests", "Alex"),
        # Other briefing verbs carry the same slot.
        ("beauftrage elis mit dem Wiki-Audit", "Ellis"),  # i18n-allow: input vocab
        ("instruct elis to read the transcripts", "Ellis"),
    ],
)
def test_briefing_verb_repairs_a_garbled_call_sign(text: str, expected: str) -> None:
    assert _addressed(text) == [expected]


def test_exact_spelling_is_unaffected() -> None:
    """The ordinary path keeps working — this only ADDS a rescued spelling."""
    spoken = "Prompte Alex, er soll die Tests fixen"  # i18n-allow: input vocab
    assert _addressed(spoken) == ["Alex"]


@pytest.mark.parametrize(
    "text",
    [
        # A briefing verb whose object is a THING, not a pane. This is the case
        # the position exemption could plausibly break, so it is pinned hardest.
        "prompte die Dokumentation neu",  # i18n-allow: input vocab
        "prompt the user for input",
        "assign the ticket to the backlog",
        # No briefing verb at all — the fuzzy band must stay shut here.
        "schreib mal die Doku",  # i18n-allow: input vocab
        "was ist die Hauptstadt von Portugal",  # i18n-allow: input vocab
    ],
)
def test_ordinary_speech_still_addresses_nothing(text: str) -> None:
    assert _addressed(text) == []


def test_a_word_far_from_the_verb_is_not_a_name() -> None:
    """The exemption is the SLOT, not the sentence.

    Without this bound, one briefing verb anywhere would license fuzzy matching
    over every remaining word — which is exactly the collision the last-resort
    branch spent a live session learning to refuse.
    """
    assert _addressed("prompt Alex and then check whether unten is still open") == [
        "Alex"
    ]
