"""Explicit routine requests must demand an actual save action."""

import pytest

from jarvis.brain.manager import _unfulfilled_replacement
from jarvis.society.routine_intent import requests_routine_creation


@pytest.mark.parametrize(
    "utterance",
    [
        "Kannst du mir eine Routine erstellen?",  # i18n-allow: input fixture
        "Erstelle die Routine.",  # i18n-allow: input fixture
        "Create a daily routine that checks GitHub issues.",
    ],
)
def test_explicit_routine_creation_is_recognized(utterance: str) -> None:
    assert requests_routine_creation(utterance)


@pytest.mark.parametrize(
    "utterance",
    [
        "Wie erstelle ich eine Routine?",  # i18n-allow: input fixture
        "Die Routine wurde erstellt.",  # i18n-allow: input fixture
        "Show my routines.",
        "Create a skill for daily checks.",
    ],
)
def test_non_creation_turns_do_not_trigger_a_write(utterance: str) -> None:
    assert not requests_routine_creation(utterance)


def test_unexecuted_routine_write_cannot_look_successful() -> None:
    answer = _unfulfilled_replacement(
        required_tool="society_propose_change",
        executed=set(),
        response_text="I will create your daily routine now.",
        suppressed=False,
        is_write=True,
        lang="en",
        domain="routine",
    )
    assert answer == "The routine was not created. The save action did not run."
