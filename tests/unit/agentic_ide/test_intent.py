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


# --------------------------------------------------------------------------- #
# Addressing SEVERAL terminals at once                                         #
# --------------------------------------------------------------------------- #
# Second live failure (voice session 2026-07-26 09:18): "Kannst du bitte Iris
# und Bruno beide in Deep Dive geben ..." reached Iris only, and the spoken
# readback then claimed both had been briefed. The detector returned the first
# match and stopped, so Bruno was never a candidate — a structural ceiling, not
# a matching accident. These guards pin the plural shape.

MULTI_NAMES = ["Iris", "Bruno", "Casey"]

# The verbatim transcript from the live failure.
LIVE_MULTI_FAILURE = (
    "Kannst du bitte Iris und Bruno beide in Deep Dive geben, dass sie "  # i18n-allow: fixture
    "unsere komplette Codebase analysieren sollen und gucken, was genau "  # i18n-allow: fixture
    "passiert und was wir genau machen muessen, um was zu verbessern"  # i18n-allow: fixture
)
# One order for both panes: only the first name carries the addressing shape.
ORDER_BOTH = (
    "Sag Iris und Bruno, sie sollen die Tests laufen lassen"  # i18n-allow: fixture
)
# Two orders in one sentence: each name carries its own directive.
ORDER_EACH = (
    "Iris soll die Tests reparieren und Bruno soll das UI pruefen"  # i18n-allow: fixture
)
QUESTION_BOTH = "Was machen Iris und Bruno?"  # i18n-allow: fixture


def test_live_multi_failure_addresses_both_terminals() -> None:
    """The exact 2026-07-26 utterance must belong to Iris AND Bruno."""
    found = intent.detect_all(LIVE_MULTI_FAILURE, names=MULTI_NAMES)
    assert [item.terminal for item in found] == ["Iris", "Bruno"]
    assert all(item.kind == intent.KIND_PROMPT for item in found)


def test_coordinated_names_share_the_instruction() -> None:
    """Only the first name carries the addressing shape; both are addressed."""
    found = intent.detect_all(ORDER_BOTH, names=MULTI_NAMES)
    assert [item.terminal for item in found] == ["Iris", "Bruno"]
    for item in found:
        assert "Tests" in item.instruction


def test_each_name_may_carry_its_own_directive() -> None:
    """Two separate assignments in one sentence stay two assignments."""
    found = intent.detect_all(ORDER_EACH, names=MULTI_NAMES)
    assert [item.terminal for item in found] == ["Iris", "Bruno"]


def test_an_unmentioned_pane_is_never_pulled_in() -> None:
    """Casey is running but not named — the fan-out must not touch it."""
    found = intent.detect_all(ORDER_BOTH, names=MULTI_NAMES)
    assert "Casey" not in [item.terminal for item in found]


def test_a_question_about_two_panes_stays_a_read() -> None:
    """Asking about two agents must not type the question into either."""
    found = intent.detect_all(QUESTION_BOTH, names=MULTI_NAMES)
    assert [item.terminal for item in found] == ["Iris", "Bruno"]
    assert all(item.kind == intent.KIND_REPORT for item in found)


@pytest.mark.parametrize(
    "utterance",
    [
        "Sagt allen, sie sollen die Tests laufen lassen",  # i18n-allow: fixture
        "Tell everyone to run the tests",
        "Alle sollen die Codebase analysieren",  # i18n-allow: fixture
    ],
)
def test_addressing_everyone_reaches_every_running_pane(utterance: str) -> None:
    """"Tell everyone ..." is the natural way to brief a whole fleet."""
    found = intent.detect_all(utterance, names=MULTI_NAMES)
    assert [item.terminal for item in found] == MULTI_NAMES
    assert all(item.kind == intent.KIND_PROMPT for item in found)


def test_detect_still_returns_the_first_match_for_existing_callers() -> None:
    """``detect`` keeps its singular contract — owns_turn and the gates rely on it."""
    found = intent.detect(LIVE_MULTI_FAILURE, names=MULTI_NAMES)
    assert found is not None
    assert found.terminal == "Iris"


def test_an_unrelated_turn_addresses_nobody() -> None:
    weather = "Wie ist das Wetter heute?"  # i18n-allow: fixture
    assert intent.detect_all(weather, names=MULTI_NAMES) == []


# --------------------------------------------------------------------------- #
# "…and split the work between you"                                            #
# --------------------------------------------------------------------------- #
# Addressing several panes does not by itself mean dividing the task: "both of
# you run the tests" is one order for two agents, and splitting it would be
# wrong. Only an explicit request to divide the work turns a fan-out into a
# planned split, because planning costs a provider call and produces DIFFERENT
# instructions per agent.


@pytest.mark.parametrize(
    "utterance",
    [
        "Teilt euch die Analyse auf verschiedene Bereiche auf",  # i18n-allow: fixture
        "Teile den Deep Dive in Aufgabenbereiche auf",  # i18n-allow: fixture
        "Split the analysis across areas",
        "Divide the work between you",
        "Each of you takes a different part",
        "Jeder von euch nimmt einen anderen Teil",  # i18n-allow: fixture
        "Reparte el trabajo entre vosotros",
    ],
)
def test_a_request_to_divide_the_work_is_recognised(utterance: str) -> None:
    assert intent.wants_split(utterance) is True


@pytest.mark.parametrize(
    "utterance",
    [
        "Sag Iris und Bruno, sie sollen die Tests laufen lassen",  # i18n-allow: fixture
        "Both of you run the test suite",
        "Analyse the codebase",
        # "aufteilen" about the CODE, not about the agents: splitting a file is
        # ordinary refactoring work and must not trigger a fleet plan.
        "Teile die grosse Datei in kleinere Module auf",  # i18n-allow: fixture
        "Split the module into two files",
    ],
)
def test_ordinary_orders_are_not_split_requests(utterance: str) -> None:
    assert intent.wants_split(utterance) is False
