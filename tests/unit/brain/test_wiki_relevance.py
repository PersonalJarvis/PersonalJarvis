"""Guards for the personal-memory relevance gate.

The headline case ("the Bugatti case", maintainer report 2026-07-25): a
general-knowledge question must never drag an unrelated personal fact into the
answer. Asking for the tallest tower in the world must not produce advice about
what the user owns.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from jarvis.brain.wiki_relevance import (
    content_terms,
    frame_context_block,
    relevant_hits,
    should_consult_memory,
)


@dataclass(frozen=True)
class FakeHit:
    """Stand-in for ``jarvis.memory.wiki.search.SearchHit`` (fakes, not mocks)."""

    title: str
    snippet: str
    score: float


# ---------------------------------------------------------------------------
# Pre-retrieval gate — general knowledge never consults the memory
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "utterance",
    [
        # The reported case, in every supported language.
        "Was ist der hoechste Turm der Welt?",  # i18n-allow: German user input under test
        "Was ist der höchste Turm der Welt?",  # i18n-allow: German user input under test
        "What is the tallest tower in the world?",
        "¿Qué es la torre más alta del mundo?",  # i18n-allow: Spanish user input under test
        # Other plain general-knowledge questions.
        "Wie funktioniert ein Dieselmotor?",  # i18n-allow: German user input under test
        "How does a diesel engine work?",
        "Who was Ada Lovelace?",
        "Explain reciprocal rank fusion.",
    ],
)
def test_general_knowledge_never_consults_memory(utterance: str) -> None:
    verdict = should_consult_memory(utterance)
    assert verdict.consult is False
    assert verdict.reason == "general_knowledge"


def test_possessive_turns_a_definitional_question_into_a_memory_question() -> None:
    """ "What are the rules" is world knowledge; "my rules" is personal."""
    assert should_consult_memory("What are the billing rules?").consult is False
    assert should_consult_memory("What are my billing rules?").consult is True


@pytest.mark.parametrize(
    "utterance",
    [
        "Wann war ich mit Viktoria essen?",  # i18n-allow: German user input under test
        "When did I meet Viktoria?",
        "Weisst du noch, wo wir letztes Jahr waren?",  # i18n-allow: German user input under test
        "Do you remember where we stayed?",
        "Wie heisst mein Zahnarzt?",  # i18n-allow: German user input under test
        "¿Te acuerdas de mi vuelo?",  # i18n-allow: Spanish user input under test
    ],
)
def test_personal_questions_do_consult_memory(utterance: str) -> None:
    assert should_consult_memory(utterance).consult is True


@pytest.mark.parametrize(
    "utterance",
    ["Hallo", "ok", "danke dir", "", "   "],  # i18n-allow: German user input under test
)
def test_smalltalk_and_fragments_skip(utterance: str) -> None:
    assert should_consult_memory(utterance).consult is False


def test_action_requests_skip() -> None:
    """An imperative is not a lookup — no memory search, no injected context."""
    turn_on_the_light_de = "Mach das Licht an"  # i18n-allow: German input under test
    assert should_consult_memory(turn_on_the_light_de).consult is False
    assert should_consult_memory("Turn on the kitchen light").consult is False


def test_gate_never_raises_on_odd_input() -> None:
    for weird in ["???", "\n\n", "🚗🚗🚗", "a b", "1 2 3 4 5"]:
        assert should_consult_memory(weird).consult in (True, False)


# ---------------------------------------------------------------------------
# Post-retrieval filter — a shared common word is not relevance
# ---------------------------------------------------------------------------


def test_hit_sharing_only_one_common_word_is_dropped() -> None:
    """The keyword index matches on ANY term; coverage is what filters."""
    hits = [
        FakeHit(title="Trestle dinner", snippet="dinner with Viktoria in San Francisco", score=0.9),
        FakeHit(title="Car collection", snippet="thoughts about the world of engines", score=0.8),
    ]
    kept = relevant_hits(hits, "dinner Viktoria Francisco")
    assert [hit.title for hit in kept] == ["Trestle dinner"]


def test_weak_hit_below_the_relative_floor_is_dropped() -> None:
    strong = FakeHit(title="Viktoria dinner", snippet="dinner with Viktoria", score=0.9)
    weak = FakeHit(title="Viktoria dinner note", snippet="dinner with Viktoria", score=0.01)
    kept = relevant_hits([strong, weak], "dinner Viktoria")
    assert kept == [strong]


def test_relative_floor_never_uses_an_absolute_cutoff() -> None:
    """Scores are only comparable within one call — a uniformly low-scoring
    call must still return its hits rather than being wiped by a fixed floor."""
    hits = [
        FakeHit(title="Viktoria dinner", snippet="dinner with Viktoria", score=0.05),
        FakeHit(title="Viktoria dinner two", snippet="dinner with Viktoria", score=0.04),
    ]
    assert len(relevant_hits(hits, "dinner Viktoria")) == 2


def test_empty_hits_and_termless_queries_are_safe() -> None:
    assert relevant_hits([], "anything") == []
    hits = [FakeHit(title="t", snippet="s", score=1.0)]
    assert relevant_hits(hits, "?? ..") == hits


def test_filter_tolerates_hits_missing_attributes() -> None:
    class Bare:
        pass

    assert relevant_hits([Bare()], "dinner Viktoria") == []


def test_content_terms_folds_and_deduplicates() -> None:
    mixed_case_de = "Über über ÜBER Reise"  # i18n-allow: German input under test
    assert content_terms(mixed_case_de) == ("uber", "reise")
    assert "?" not in "".join(content_terms("Wann? Wo?"))  # i18n-allow: German input


# ---------------------------------------------------------------------------
# Framing — the block must grant permission to ignore itself
# ---------------------------------------------------------------------------


def test_framed_block_tells_the_model_it_may_be_irrelevant() -> None:
    block = frame_context_block(["**Cars**: six of them"])
    assert "**Cars**: six of them" in block
    lowered = block.lower()
    assert "ignore it completely" in lowered
    assert "may have nothing to do" in lowered


def test_framing_an_empty_list_yields_nothing_to_append() -> None:
    assert frame_context_block([]) == ""
