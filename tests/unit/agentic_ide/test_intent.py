"""Guards for the Agentic-IDE turn detector.

The regression this file exists for (voice session 2026-07-25 15:47): the user
said "Kannst du mal bitte schnell ein zu Dana ein Review schicken und zwar dass  # i18n-allow: German speech input under test
er ein Deep Dive machen soll ..." and Jarvis dispatched a background Codex
worker into a fresh git worktree while the terminal called Dana sat idle. The
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

NAMES = ["Alex", "Blake", "Casey", "Dana"]

# The verbatim transcript from the live failure, truncated the way the log
# recorded it.
LIVE_FAILURE = (
    "Kannst du mal bitte schnell ein zu Dana ein Review schicken und zwar dass "  # i18n-allow: German speech input under test
    "er ein Deep Dive machen soll und dann kompletten Deep Dive machen und "
    "gucken ob irgendwo Fehler sind"
)


def test_live_failure_reaches_the_terminal_not_a_background_worker() -> None:
    """The exact 2026-07-25 utterance must belong to Dana."""
    found = intent.detect(LIVE_FAILURE, names=NAMES)
    assert found is not None
    assert found.terminal == "Dana"
    assert found.kind == intent.KIND_PROMPT
    # And the router's guard must agree, or force-spawn wins again.
    assert intent.owns_turn(LIVE_FAILURE, names=NAMES) is True


@pytest.mark.parametrize(
    ("utterance", "terminal"),
    [
        ("Sag Alex, sie soll die Tests laufen lassen", "Alex"),  # i18n-allow: German speech input under test
        ("Tell Blake to refactor the wake word provider", "Blake"),
        ("Blake should look at the vosk provider", "Blake"),
        ("Casey, mach mal einen Review vom Audio-Code", "Casey"),  # i18n-allow: German speech input under test
        ("Schick das an Dana", "Dana"),
        ("Dile a Dana que revise el codigo", "Dana"),
        ("Lass Alex den Bug im Wake-Pfad untersuchen", "Alex"),
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
        ("Was macht Alex gerade?", "Alex"),
        ("What is Dana doing?", "Dana"),
        ("Ist Blake fertig?", "Blake"),  # i18n-allow: German speech input under test
        ("Wie ist der Status von Casey?", "Casey"),  # i18n-allow: German speech input under test
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
        "Spawne einen Subagenten der Dana hilft",  # i18n-allow: German speech input under test
        "Start a background agent to review this",
        "Mach das im Hintergrund, Alex braucht das nicht",  # i18n-allow: German speech input under test
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
        "Dana ist ein schoener Name fuer ein Kind",  # i18n-allow: German speech input under test
        "Erklaer mir bitte wie Vosk funktioniert",
    ],
)
def test_unrelated_turns_are_left_alone(utterance: str) -> None:
    """A passing mention or an unrelated request is none of the detector's business."""
    assert intent.detect(utterance, names=NAMES) is None
    assert intent.owns_turn(utterance, names=NAMES) is False


def test_no_open_workspace_means_no_claim() -> None:
    """With no terminals running, nothing can be addressed."""
    assert intent.detect("Sag Alex, sie soll die Tests starten", names=[]) is None  # i18n-allow: German speech input under test


def test_call_signs_are_read_from_the_session_not_a_fixed_list() -> None:
    """A workspace with custom names behaves exactly like one with defaults."""
    custom = ["Hunter", "Ivy"]
    found = intent.detect("Sag Hunter, er soll die Tests starten", names=custom)  # i18n-allow: German speech input under test
    assert found is not None
    assert found.terminal == "Hunter"
    # A default-pool name that is NOT in this workspace must not match.
    assert intent.detect("Sag Alex, sie soll die Tests starten", names=custom) is None  # i18n-allow: German speech input under test


def test_instruction_keeps_the_work_when_stripping_would_eat_it() -> None:
    """A short utterance falls back to the full text rather than a stub."""
    found = intent.detect("Schick das an Dana", names=NAMES)
    assert found is not None
    # "das" alone would be useless to the composer; the whole sentence is honest.
    assert len(found.instruction) >= len("Schick das an Dana")


def test_instruction_drops_the_addressing_when_there_is_real_work_left() -> None:
    found = intent.detect(
        "Sag Alex, sie soll die Wake-Word-Erkennung reparieren", names=NAMES  # i18n-allow: German speech input under test
    )
    assert found is not None
    assert "Alex" not in found.instruction
    assert "Wake-Word-Erkennung" in found.instruction
