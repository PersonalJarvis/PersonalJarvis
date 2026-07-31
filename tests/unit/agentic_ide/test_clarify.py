"""A misheard call-sign must ask, and only ever ask about the workspace.

Two live failures of 2026-07-27 meet in this module, and they pull in opposite
directions — which is why the cases below are written as two lists that must
BOTH hold, rather than as one threshold:

* the pane "Ellis" came back from speech recognition as "Ilies" and the
  addressed-terminal path stood down in silence, so nothing was typed anywhere
  and the live model claimed an agent was working;
* ordinary speech reaches the same pool from the other side ("allen" scores
  0.750 against "Alex", above the acting threshold), so a sentence about the
  outside world must never be typed into somebody's coding agent.

The maintainer's own examples are pinned verbatim, including the counter-example
("what is Elon Musk up to?") — they are the specification.
"""

from __future__ import annotations

import pytest

from jarvis.agentic_ide import intent
from jarvis.agentic_ide.clarify import (
    ClarificationWindow,
    addresses_workspace,
    detect_clarification,
    is_outside_world_talk,
)

PANES = ["Alex", "Blake", "Casey", "Dana", "Ellis", "Finn"]


def _outcome(text: str, names: list[str] | None = None) -> str:
    """What the workspace does with this turn: act, ask, or stay out of it.

    Deliberately routed through BOTH detectors in the order the brain calls
    them. A case that acts must not also ask, and a case that neither acts nor
    asks must be silent for a reason — the three outcomes are only meaningful
    together.
    """
    panes = names or PANES
    if intent.detect_all(text, names=panes):
        return "act"
    return "ask" if detect_clarification(text, names=panes) else "silent"


# --------------------------------------------------------------------------- #
# A turn addresses a FLEET (live 2026-07-27 19:07)                             #
# --------------------------------------------------------------------------- #


def test_a_garbled_name_beside_a_certain_one_is_still_asked_about() -> None:
    """The 19:07 failure: two panes addressed, one briefed, one dropped silent.

    A certainly-named pane used to end the search, so the second addressee —
    whose call-sign speech recognition had mangled — produced no action and no
    question. The user heard that Alex was working and believed both were.
    """
    need = detect_clarification(
        "Alex und Blaike sollen beide einen Deep Dive machen", names=PANES
    )
    assert need is not None, "the second addressee must not vanish"
    assert need.certain == ("Alex",), "the pane that WAS understood is carried"
    assert need.candidates == ("Blake",)
    assert need.spoken == "Blaike"


def test_two_garbled_names_in_one_list_both_get_asked_about() -> None:
    """"Alexa und Blaike" is one instruction for two agents, not noise."""
    need = detect_clarification(
        "Alexa und Blaike sollen beide einen Deep Dive machen", names=PANES
    )
    assert need is not None
    assert [item.spoken for item in need.uncertain] == ["Alexa", "Blaike"]
    assert need.offered == ("Alex", "Blake")

    window = ClarificationWindow()
    window.arm(need)
    # ONE "yes" confirms the whole list — that is the point of asking once.
    assert window.resolve_answer("ja") == (("Alex", "Blake"), need.utterance)


def test_naming_both_panes_answers_for_both() -> None:
    need = detect_clarification(
        "Alexa und Blaike sollen beide einen Deep Dive machen", names=PANES
    )
    assert need is not None
    window = ClarificationWindow()
    window.arm(need)
    resolved = window.resolve_answer("Alex and Blake")
    assert resolved is not None
    assert resolved[0] == ("Alex", "Blake")


def test_a_stray_near_miss_beside_a_working_turn_stays_silent() -> None:
    """The cost of the change must not be a question on every good turn.

    "Alex" is certain and the sentence works; "Kannst" scores against "Casey"
    without being listed beside anything, so it is noise and must not produce
    a question the user then has to say "no" to.
    """
    need = detect_clarification(
        "Alex soll bitte die Tests reparieren, Kannst das laufen", names=PANES
    )
    assert need is None


# --------------------------------------------------------------------------- #
# The live failure                                                             #
# --------------------------------------------------------------------------- #


def test_the_live_transcript_asks_instead_of_going_silent() -> None:
    """The 2026-07-27 16:18 turn, verbatim from the session transcript."""
    spoken = (
        "kannst du bitte Ilies ein Deep Dive machen lassen und sie soll "
        "gucken, wieso wenn man aus dem Agentic IDE Mode rausgeht"
    )
    need = detect_clarification(spoken, names=PANES)
    assert need is not None, "the turn that started this must not be silent"
    assert need.candidates == ("Ellis",)
    assert need.spoken == "Ilies"
    # The ORIGINAL wording is carried, because it holds the actual task.
    assert need.utterance == spoken


def test_the_answer_delivers_the_original_task() -> None:
    """A question is only worth asking if "yes" still does the work."""
    window = ClarificationWindow()
    spoken = "kannst du bitte Ilies das Deep Dive machen lassen"
    need = detect_clarification(spoken, names=PANES)
    assert need is not None
    window.arm(need)

    assert window.resolve_answer("ja") == (("Ellis",), spoken)
    # One question, one answer: the window is spent.
    assert not window.armed
    assert window.resolve_answer("ja") is None


def test_naming_the_pane_answers_even_with_several_offered() -> None:
    """"Maggie" decides what a bare "yes" cannot."""
    window = ClarificationWindow()
    panes = ["Alex", "Max", "Maggie"]
    need = detect_clarification("Kannst du bitte Mags prompten?", names=panes)
    assert need is not None
    assert set(need.candidates) == {"Max", "Maggie"}
    window.arm(need)

    resolved = window.resolve_answer("Maggie")
    assert resolved is not None
    assert resolved[0] == ("Maggie",)


def test_a_bare_yes_cannot_choose_between_two_panes() -> None:
    window = ClarificationWindow()
    need = detect_clarification(
        "Kannst du bitte Mags prompten?", names=["Alex", "Max", "Maggie"]
    )
    assert need is not None
    window.arm(need)
    assert window.resolve_answer("ja") is None


def test_a_declined_question_delivers_nothing() -> None:
    window = ClarificationWindow()
    need = detect_clarification("Kannst du bitte Ilies prompten?", names=PANES)
    assert need is not None
    window.arm(need)
    assert window.resolve_answer("nein") is None
    assert not window.armed


def test_moving_on_closes_the_question_instead_of_answering_it() -> None:
    """A full sentence is the user moving on, not an answer."""
    window = ClarificationWindow()
    need = detect_clarification("Kannst du bitte Ilies prompten?", names=PANES)
    assert need is not None
    window.arm(need)
    assert (
        window.resolve_answer(
            "ach lass mal, erzähl mir lieber wie das Wetter morgen wird"
        )
        is None
    )
    assert not window.armed


def test_a_stale_question_never_delivers_work() -> None:
    window = ClarificationWindow()
    need = detect_clarification("Kannst du bitte Ilies prompten?", names=PANES)
    assert need is not None
    window.arm(need, now=0.0)
    assert window.resolve_answer("ja", now=10_000.0) is None


# --------------------------------------------------------------------------- #
# The maintainer's specification (2026-07-27)                                  #
# --------------------------------------------------------------------------- #

# "If I only say a first name that could be roughly the same, it should check
# that I mean the right one."
ASKS = [
    ("kannst du bitte Ilies ein Deep Dive machen lassen", None),
    ("Ilies soll mal einen Deep Dive machen", None),
    ("Kannst du bitte Mags prompten?", ["Alex", "Max", "Maggie"]),
    ("Dena soll die Tests fixen", None),
    ("Kannst du Ellice beauftragen, den Bug zu fixen", None),
]

# "But not, for example: 'can you tell me what Elon Musk is doing right now?'
# … and then it prompts everyone. That must of course not happen."
STAYS_OUT = [
    "Kannst du bitte mir sagen, was Elon Musk gerade macht?",
    "Sag mir was Elon Musk gerade macht",
    "Was macht Elon gerade?",
    "Wer ist Elon Musk?",
    "kannst du mir sagen was Barack Obama gerade macht",
    "Erzähl mir was über Elias Canetti",
    "wie ist das Wetter heute",
    "kannst du bitte den Bug fixen",
    "mach mal einen Deep Dive in den Code",
    "Ilies ist ein schöner Name",
    "schick das an Alexander Müller weiter",
    "lass die Tests laufen und fixe die Fehler",
    "Das soll man mal fixen",
]


@pytest.mark.parametrize(("spoken", "panes"), ASKS)
def test_an_uncertain_call_sign_asks(spoken: str, panes: list[str] | None) -> None:
    assert _outcome(spoken, panes) == "ask", spoken


@pytest.mark.parametrize("spoken", STAYS_OUT)
def test_the_outside_world_never_reaches_a_pane(spoken: str) -> None:
    assert _outcome(spoken) == "silent", spoken


# --------------------------------------------------------------------------- #
# Certainty still acts — asking must not become the new normal                 #
# --------------------------------------------------------------------------- #

ACTS = [
    "sag Ellis, sie soll die Tests fixen",
    # Same sound, different spelling: certain, and therefore acted on. This is
    # the gap the canonicalization in ``intent`` closed — the templates are
    # built from the exact spelling and used to match none of these.
    "Sag Elis, sie soll die Tests fixen",
    "beauftrage Ellys mit dem Bug",
    "Kannst du Elliss beauftragen, den Bug zu fixen",
    "Was macht Ellis gerade?",
    "sag allen, sie sollen die Tests fixen",
]


@pytest.mark.parametrize("spoken", ACTS)
def test_a_certain_call_sign_is_still_acted_on(spoken: str) -> None:
    assert _outcome(spoken) == "act", spoken


def test_a_misheard_collective_address_cannot_brief_the_whole_workspace() -> None:
    """The costliest thing a garbled word can do is reach EVERY pane at once."""
    assert is_outside_world_talk("sag allen was Elon Musk gerade macht")
    assert intent.detect_all(
        "sag allen was Elon Musk gerade macht", names=PANES
    ) == []
    # A genuine collective instruction still reaches everyone.
    assert len(intent.detect_all("sag allen, sie sollen aufhören", names=PANES)) == len(
        PANES
    )


# --------------------------------------------------------------------------- #
# The gate itself                                                              #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("spoken", "expected"),
    [
        ("Kannst du bitte Max prompten?", True),
        ("beauftrage Max mit dem Bug", True),
        ("sag Max, er soll die Tests fixen", True),
        ("Max soll die Tests fixen", True),
        # A handover verb and a name and nothing else is NOT workspace evidence.
        ("Sag mir was Elon Musk gerade macht", False),
        ("wie ist das Wetter", False),
    ],
)
def test_workspace_gate(spoken: str, expected: bool) -> None:
    assert addresses_workspace(spoken) is expected


def test_an_all_lowercase_transcript_still_refuses_function_words() -> None:
    """Case cannot rule when the provider capitalizes nothing; stopwords still do."""
    need = detect_clarification(
        "kannst du bitte den bug fixen lassen", names=PANES
    )
    assert need is None


# --------------------------------------------------------------------------- #
# Asking a pane for a status report                                            #
# --------------------------------------------------------------------------- #
# The 16:53 half of the same day: "Was hat Dana gemacht?" carries no repo, no
# test, no bug and no branch, so the coding-work pairings never saw it — a
# garbled call-sign inside the single most common workspace question there is
# went silently nowhere.


@pytest.mark.parametrize(
    "spoken",
    [
        "Was hat Ellis gemacht?",
        "Was macht Ellis gerade?",  # i18n-allow: German speech input under test
        "Wie weit ist Ellis?",  # i18n-allow: German speech input under test
        "What has Ellis done?",
        "How far is Ellis?",
        "Que ha hecho Ellis?",
    ],
)
def test_a_status_question_addresses_the_workspace(spoken: str) -> None:
    assert addresses_workspace(spoken) is True


def test_a_garbled_call_sign_in_a_status_question_asks() -> None:
    """The live 16:53 shape, with the name mangled the way 16:18 mangled it."""
    need = detect_clarification("Was hat Ilies gemacht?", names=PANES)
    assert need is not None
    assert need.candidates == ("Ellis",)
    # And the confirmed pane is briefed with the ORIGINAL question, not "yes".
    window = ClarificationWindow()
    window.arm(need)
    assert window.resolve_answer("ja") == (("Ellis",), "Was hat Ilies gemacht?")


@pytest.mark.parametrize(
    "spoken",
    [
        # A full personal name turns the identical shape into a question about
        # a human being — the surname is the only thing that tells them apart.
        "Sag mir was Elon Musk gerade macht",
        "Was hat Angela Merkel gemacht?",
    ],
)
def test_a_status_question_about_a_person_stays_out_of_the_workspace(
    spoken: str,
) -> None:
    assert addresses_workspace(spoken) is False
    assert _outcome(spoken) == "silent"
