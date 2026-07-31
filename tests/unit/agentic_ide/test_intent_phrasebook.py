"""A phrasebook guard: does the workspace understand how people actually talk?

The other intent tests each pin ONE shape that once broke. This file asks the
broader question that keeps producing those breaks — given the many ways a
person can hand work to a coding agent, ask what it is doing, or ask for more
panes, does every one of them reach the right path?

It exists because the failures in this area all look the same from the outside
and all have the same cause: a phrasing nobody had written a template for
produces NO match, the deterministic fast path stands down without a word, and
the live model fills the silence by claiming something happened. The user hears
a confident sentence and finds an idle terminal. A miss here is never visible
as a miss, which is exactly why it needs a broad, boring guard rather than
another single-case regression test.

Two properties make this a phrasebook rather than a wishlist:

* every case is routed through ``_classify``, which mirrors the ORDER the brain
  actually applies (close, then spawn, then addressed delivery) — a detector
  that answers correctly in isolation but loses the turn to an earlier gate is
  still a bug, and only the ordered view can see it;
* the negative cases carry the same weight as the positive ones. Widening a
  detector until every phrasing lands is easy and wrong: a false positive types
  a stranger's sentence into a running coding agent, which is worse than any
  miss. The unrelated block is what keeps the widening honest.

German and Spanish utterances here are speech-recognition input vocabulary —
the words a person literally says — not prose (CLAUDE.md §1, closed list item
3). They are the content under test.
"""
from __future__ import annotations

import pytest

from jarvis.agentic_ide import intent

NAMES = ["Alex", "Blake", "Casey", "Dana"]


def _classify(text: str) -> str:
    """The path this utterance takes, in the order the brain applies them.

    Mirrors ``BrainManager``: the close fast path runs first (so "all" cannot
    become a prompt typed into the very panes being stopped), then the spawn
    fast path, then addressed delivery. Returns ``"none"`` when the workspace
    declines the turn.
    """
    if intent.detect_close_fleet(text) is not None:
        return "close"
    if intent.detect_spawn(text, names=NAMES) is not None:
        return "spawn"
    found = intent.detect_all(text, names=NAMES)
    return found[0].kind if found else "none"


# --------------------------------------------------------------------------- #
# Handing work to a named pane                                                 #
# --------------------------------------------------------------------------- #

PROMPTS_DE = [
    "Sag Alex, er soll die Tests laufen lassen",
    "Alex soll mal schauen warum der Wake-Pfad nicht geht",
    "Gib Alex die Aufgabe den Bug zu fixen",
    "Alex, mach mal einen Review vom Audio-Code",
    "Lass Alex das übernehmen und die Tests fixen",
    "Beauftrage Alex mit dem Refactoring",
    "Setz Alex mal auf den Wake-Bug an",
    "Alex übernimmt den Wake-Bug",
    "Alex müsste mal die Doku aktualisieren",
    "Alex darf gerne den Wake-Bug untersuchen",
    "Ich möchte dass Alex den Wake-Pfad prüft",
    # The modal in FRONT of the call-sign — the ordinary spoken order, and the
    # one every name-anchored template used to miss.
    "Kann Alex mal die Tests fixen?",
    "Könnte Alex bitte den Code prüfen?",
    # The German sentence bracket: the handing-over verb lands at the end.
    "Kannst du Alex bitte sagen, dass er einen Deep Dive machen soll?",
    "Würdest du Alex bitten die Tests zu fixen?",
]

PROMPTS_EN = [
    "Tell Alex to run the tests",
    "Alex should look into the wake path",
    "Have Alex review the audio code",
    "Get Alex to fix the failing test",
    "Ask Alex to check the config",
    "Can Alex look at the wake bug?",
    "Could Alex please check the vosk provider?",
    "I want Alex to analyze the codebase",
    "I need Alex to fix the wake path",
    "Put Alex on the security audit",
    "Point Alex at the security audit",
    "Alex, take a look at the vosk provider",
    "Would you have Alex do a deep dive on the audio code?",
    "Alex needs to fix the failing tests",
    "Let's have Alex take the wake path",
]

PROMPTS_ES = [
    "Dile a Alex que revise el codigo",
    "Alex deberia revisar el codigo de audio",
    "Pidele a Alex que arregle los tests",
    "Que Alex analice el area de audio",
    "Encargale a Alex la auditoria de seguridad",
    "Puede Alex revisar el wake path?",
]


@pytest.mark.parametrize(
    "utterance", PROMPTS_DE + PROMPTS_EN + PROMPTS_ES  # i18n-allow: input vocab
)
def test_work_handed_to_a_named_pane_is_delivered(utterance: str) -> None:
    """Every way of assigning work must reach the pane, not a status read."""
    assert _classify(utterance) == intent.KIND_PROMPT, utterance
    found = intent.detect(utterance, names=NAMES)
    assert found is not None and found.terminal == "Alex", utterance
    # The router's precedence gate has to agree, or force-spawn takes the turn
    # and the work becomes an invisible background mission.
    assert intent.owns_turn(utterance, names=NAMES) is True, utterance


# --------------------------------------------------------------------------- #
# Asking what a pane is up to                                                  #
# --------------------------------------------------------------------------- #

REPORTS = [
    "Was macht Alex gerade?",  # i18n-allow: input vocab
    "Ist Alex fertig?",  # i18n-allow: input vocab
    "Wie weit ist Alex?",  # i18n-allow: input vocab
    "Was hat Alex rausgefunden?",  # i18n-allow: input vocab
    "Läuft Alex noch?",  # i18n-allow: input vocab
    "Hat Alex schon was gefunden?",  # i18n-allow: input vocab
    "Alex Status?",
    "What is Alex doing?",
    "Is Alex done yet?",
    "Any progress from Alex?",
    "Que hizo Alex?",
    # Politeness does not make a question an order — "ask" is deliberately not
    # a briefing verb, because this is a read and typing it into Alex would
    # both lose the answer and waste the agent's turn.
    "Kannst du Alex fragen, was er macht?",  # i18n-allow: input vocab
]


@pytest.mark.parametrize("utterance", REPORTS)
def test_questions_about_a_pane_stay_read_only(utterance: str) -> None:
    """A status question must never be typed into the agent it asks about."""
    assert _classify(utterance) == intent.KIND_REPORT, utterance


# --------------------------------------------------------------------------- #
# Asking for more panes, and for fewer                                         #
# --------------------------------------------------------------------------- #

SPAWNS = [
    "Mach mal drei neue Terminals auf",  # i18n-allow: input vocab
    "Spawne fünf Claude Code Terminals",  # i18n-allow: input vocab
    "Öffne noch zwei Terminals",  # i18n-allow: input vocab
    "Ich brauche noch ein Terminal",  # i18n-allow: input vocab
    "Gib mir drei Codex Terminals",  # i18n-allow: input vocab
    "Ich hätte gern noch zwei Claude Terminals",  # i18n-allow: input vocab
    "Open three more terminals",
    "Can you open five terminals?",
    "Add two more panes",
    "Abre tres terminales mas",
    "Starte fünf Agenten die den Code analysieren und teilt euch auf",  # i18n-allow: input vocab
]


@pytest.mark.parametrize("utterance", SPAWNS)
def test_asking_for_more_panes_opens_them(utterance: str) -> None:
    assert _classify(utterance) == "spawn", utterance


CLOSES = [
    "Schließ alle Terminals",  # i18n-allow: input vocab
    # German closes with a separable verb, so the particle lands at the end and
    # the stem is the OPEN verb. This exact sentence used to open panes.
    "Mach alle Terminals zu",  # i18n-allow: input vocab
    "Close all the terminals",
    "Cierra todos los terminales",
]


@pytest.mark.parametrize("utterance", CLOSES)
def test_closing_the_fleet_never_opens_more(utterance: str) -> None:
    """Watching the opposite of your request happen is the worst kind of miss."""
    assert _classify(utterance) == "close", utterance


def test_a_spoken_count_survives_every_umlaut_spelling() -> None:
    """The same request must open the same number of panes however it is typed.

    Voice transcripts carry the real character; typed turns reach the very same
    detectors from a keyboard without a German layout.
    """
    for spelling in ("fünf", "funf", "fuenf"):  # i18n-allow: input vocab
        request = intent.detect_spawn(
            f"Mach mal {spelling} neue Terminals auf", names=NAMES  # i18n-allow: input vocab
        )
        assert request is not None, spelling
        assert request.count == 5, spelling


# --------------------------------------------------------------------------- #
# What the workspace must keep its hands off                                   #
# --------------------------------------------------------------------------- #
# The counterweight to everything above. Each widening of a detector is only
# safe while these still answer "none": a false positive types a stranger's
# sentence into a running coding agent, which no user can undo.

UNRELATED = [
    "Wie ist das Wetter heute?",  # i18n-allow: input vocab
    "Was hat Elon Musk gemacht?",  # i18n-allow: input vocab
    "Erklär mir bitte wie Vosk funktioniert",  # i18n-allow: input vocab
    "Alex ist ein schöner Name für ein Kind",  # i18n-allow: input vocab
    "Mach einen Deep Dive in meine Google Cloud Kosten",  # i18n-allow: input vocab
    "Was hat Alex Schmidt gestern gesagt?",  # i18n-allow: input vocab
    "Kannst du prüfen ob der Server läuft?",  # i18n-allow: input vocab
]


@pytest.mark.parametrize("utterance", UNRELATED)
def test_turns_that_are_not_the_workspaces_are_left_alone(utterance: str) -> None:
    assert _classify(utterance) == "none", utterance
    assert intent.owns_turn(utterance, names=NAMES) is False, utterance


SPAWN_VEHICLE = [
    "Spawne einen Subagenten der Alex hilft",  # i18n-allow: input vocab
    "Start a background agent to review this",
    "Delegiere das an einen Worker",  # i18n-allow: input vocab
]


@pytest.mark.parametrize("utterance", SPAWN_VEHICLE)
def test_an_explicit_background_request_still_gets_a_worker(utterance: str) -> None:
    """Widening the workspace must not swallow a genuine mission request."""
    assert intent.owns_turn(utterance, names=NAMES) is False, utterance
