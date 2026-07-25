"""Guards for the Agentic-IDE turn detector.

The regression this file exists for (voice session 2026-07-25 15:47): the user
said "Kannst du mal bitte schnell ein zu Kai ein Review schicken und zwar dass
er ein Deep Dive machen soll ..." and Jarvis dispatched a background Codex
worker into a fresh git worktree while the terminal called Kai sat idle. The
utterance carried the depth marker "Deep Dive", the router's force-spawn hoist
matched it, and the workspace never got a look in.

Two properties are pinned here, and they pull against each other on purpose:

* an addressed terminal claims the turn even when the sentence is full of
  delegation-flavoured words, and
* naming the spawn vehicle outright still reaches the spawn path — otherwise
  the fix would simply have broken background agents whenever a workspace
  happens to be open.
"""
from __future__ import annotations

import pytest

from jarvis.agentic_ide import intent

NAMES = ["Mika", "Nova", "Aria", "Kai"]

# The verbatim transcript from the live failure, truncated the way the log
# recorded it.
LIVE_FAILURE = (
    "Kannst du mal bitte schnell ein zu Kai ein Review schicken und zwar dass "
    "er ein Deep Dive machen soll und dann kompletten Deep Dive machen und "
    "gucken ob irgendwo Fehler sind"
)


def test_live_failure_reaches_the_terminal_not_a_background_worker() -> None:
    """The exact 2026-07-25 utterance must belong to Kai."""
    found = intent.detect(LIVE_FAILURE, names=NAMES)
    assert found is not None
    assert found.terminal == "Kai"
    assert found.kind == intent.KIND_PROMPT
    # And the router's guard must agree, or force-spawn wins again.
    assert intent.owns_turn(LIVE_FAILURE, names=NAMES) is True


@pytest.mark.parametrize(
    ("utterance", "terminal"),
    [
        ("Sag Mika, sie soll die Tests laufen lassen", "Mika"),
        ("Tell Nova to refactor the wake word provider", "Nova"),
        ("Nova should look at the vosk provider", "Nova"),
        ("Aria, mach mal einen Review vom Audio-Code", "Aria"),
        ("Schick das an Kai", "Kai"),
        ("Dile a Kai que revise el codigo", "Kai"),
        ("Lass Mika den Bug im Wake-Pfad untersuchen", "Mika"),
    ],
)
def test_addressing_shapes_across_locales(utterance: str, terminal: str) -> None:
    found = intent.detect(utterance, names=NAMES)
    assert found is not None, utterance
    assert found.terminal == terminal
    assert found.kind == intent.KIND_PROMPT


@pytest.mark.parametrize(
    ("utterance", "terminal"),
    [
        ("Was macht Mika gerade?", "Mika"),
        ("What is Kai doing?", "Kai"),
        ("Ist Nova fertig?", "Nova"),
        ("Wie ist der Status von Aria?", "Aria"),
    ],
)
def test_questions_about_a_pane_are_reads_not_prompts(
    utterance: str, terminal: str
) -> None:
    """Asking what an agent is doing must never type the question into it."""
    found = intent.detect(utterance, names=NAMES)
    assert found is not None, utterance
    assert found.terminal == terminal
    assert found.kind == intent.KIND_REPORT


@pytest.mark.parametrize(
    "utterance",
    [
        "Spawne einen Subagenten der Kai hilft",
        "Start a background agent to review this",
        "Mach das im Hintergrund, Mika braucht das nicht",
        "Delegiere das an einen Worker",
    ],
)
def test_naming_the_spawn_vehicle_still_wins(utterance: str) -> None:
    """A workspace being open must not swallow an explicit delegation request."""
    assert intent.owns_turn(utterance, names=NAMES) is False


@pytest.mark.parametrize(
    "utterance",
    [
        "Wie ist das Wetter heute?",
        "Mach einen Deep Dive in meine Google Cloud Kosten",
        "Kai ist ein schoener Name fuer ein Kind",
        "Erklaer mir bitte wie Vosk funktioniert",
    ],
)
def test_unrelated_turns_are_left_alone(utterance: str) -> None:
    """A passing mention or an unrelated request is none of the detector's business."""
    assert intent.detect(utterance, names=NAMES) is None
    assert intent.owns_turn(utterance, names=NAMES) is False


def test_no_open_workspace_means_no_claim() -> None:
    """With no terminals running, nothing can be addressed."""
    assert intent.detect("Sag Mika, sie soll die Tests starten", names=[]) is None


def test_call_signs_are_read_from_the_session_not_a_fixed_list() -> None:
    """A workspace with custom names behaves exactly like one with defaults."""
    custom = ["Bruno", "Vega"]
    found = intent.detect("Sag Bruno, er soll die Tests starten", names=custom)
    assert found is not None
    assert found.terminal == "Bruno"
    # A default-pool name that is NOT in this workspace must not match.
    assert intent.detect("Sag Mika, sie soll die Tests starten", names=custom) is None


def test_instruction_keeps_the_work_when_stripping_would_eat_it() -> None:
    """A short utterance falls back to the full text rather than a stub."""
    found = intent.detect("Schick das an Kai", names=NAMES)
    assert found is not None
    # "das" alone would be useless to the composer; the whole sentence is honest.
    assert len(found.instruction) >= len("Schick das an Kai")


def test_instruction_drops_the_addressing_when_there_is_real_work_left() -> None:
    found = intent.detect(
        "Sag Mika, sie soll die Wake-Word-Erkennung reparieren", names=NAMES
    )
    assert found is not None
    assert "Mika" not in found.instruction
    assert "Wake-Word-Erkennung" in found.instruction
