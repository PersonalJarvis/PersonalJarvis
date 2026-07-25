"""The classifier that decides which guardrails a composed prompt carries."""
from __future__ import annotations

import pytest

from jarvis.agentic_ide.task_kind import (
    KIND_IMPLEMENT,
    KIND_INVESTIGATE,
    KIND_NEUTRAL,
    KIND_QUESTION,
    KIND_REVIEW,
    classify,
)


@pytest.mark.parametrize(
    ("instruction", "expected"),
    [
        # implement
        ("add a retry to the upload path", KIND_IMPLEMENT),
        ("baue einen Retry in den Upload-Pfad ein", KIND_IMPLEMENT),  # i18n-allow
        ("implementa un reintento en la subida", KIND_IMPLEMENT),
        ("refactor the session registry", KIND_IMPLEMENT),
        ("fix the crash on an empty folder", KIND_IMPLEMENT),
        # review
        ("review the ranking pipeline", KIND_REVIEW),
        ("mach ein Code-Review der Ranking-Pipeline", KIND_REVIEW),  # i18n-allow
        ("revisa el pipeline de ranking", KIND_REVIEW),
        ("audit the credential storage for leaks", KIND_REVIEW),
        # investigate
        ("find out why the wake word stopped firing", KIND_INVESTIGATE),
        # i18n-allow: German utterance under test
        ("finde heraus warum das Wake-Wort nicht mehr auslöst", KIND_INVESTIGATE),  # i18n-allow
        ("averigua por qué falla el arranque", KIND_INVESTIGATE),
        ("debug the stalled prompt submission", KIND_INVESTIGATE),
        # question
        ("how does the file index rank candidates", KIND_QUESTION),
        ("wie funktioniert der File-Index", KIND_QUESTION),
        ("qué hace el índice de archivos", KIND_QUESTION),
        ("explain the fallback chain", KIND_QUESTION),
    ],
)
def test_classifies_each_kind_in_every_supported_locale(instruction, expected):
    assert classify(instruction) == expected


def test_unrecognised_instruction_falls_to_the_neutral_set():
    assert classify("the thing with the stuff over there") == KIND_NEUTRAL


def test_empty_instruction_is_neutral():
    assert classify("") == KIND_NEUTRAL
    assert classify("   ") == KIND_NEUTRAL


def test_an_earlier_verb_wins_over_a_later_incidental_mention():
    # "review" appears, but the ask is to build the reviewer.
    assert classify("build a review dashboard for the mission log") == KIND_IMPLEMENT


def test_a_question_shape_beats_a_verb_buried_in_the_sentence():
    assert classify("why does the composer add the file reference last") == (
        KIND_INVESTIGATE
    )
