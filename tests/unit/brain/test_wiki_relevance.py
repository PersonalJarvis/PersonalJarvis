"""Guards for the personal-memory relevance gate.

The headline case ("the Bugatti case", maintainer report 2026-07-25): a
general-knowledge question must never drag an unrelated personal fact into the
answer. Asking for the tallest tower in the world must not produce advice about
what the user owns.

Since the 2026-08-04 recall audit the gate is retrieval-first: the defense
against the Bugatti case is no longer a refusal to search but the STRICT
coverage bar (``MemoryVerdict.strict``) — world-shaped turns search too, and
only a page covering nearly the whole question may be injected.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from jarvis.brain.wiki_relevance import (
    DEFAULT_STRICT_MIN_COVERAGE,
    content_terms,
    fold_text,
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
# Pre-retrieval gate — general knowledge searches, but only behind the strict bar
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
def test_general_knowledge_probes_only_behind_the_strict_bar(utterance: str) -> None:
    """Retrieval-first: the turn may search ("who is X" can be a vault
    person), but anything found must clear the strict coverage bar."""
    verdict = should_consult_memory(utterance)
    assert verdict.consult is True
    assert verdict.strict is True
    assert verdict.reason == "world_shape_probe"


def test_possessive_relaxes_the_bar_of_a_definitional_question() -> None:
    """ "What are the rules" is world-shaped (strict); "my rules" is personal."""
    world = should_consult_memory("What are the billing rules?")
    assert (world.consult, world.strict) == (True, True)
    personal = should_consult_memory("What are my billing rules?")
    assert (personal.consult, personal.strict) == (True, False)


@pytest.mark.parametrize(
    "utterance",
    [
        "Wann war ich mit Beispielkontakt essen?",  # i18n-allow: German input
        "When did I meet Example Contact?",
        "Weisst du noch, wo wir letztes Jahr waren?",  # i18n-allow: German user input under test
        "Do you remember where we stayed?",
        "Wie heisst mein Zahnarzt?",  # i18n-allow: German user input under test
        "¿Te acuerdas de mi vuelo?",  # i18n-allow: Spanish user input under test
        # REAL umlaut spellings, exactly as STT produces them. Regression for
        # the fold defect: NFKD alone turned "wofür" into "wofur", which the
        # pre-folded vocabulary ("wofuer") could never match — every
        # umlaut-carrying German phrase was dead against real speech.
        "Wofür brauche ich meinen Zweitrechner?",  # i18n-allow: German user input under test
        "Worüber haben wir gestern gesprochen?",  # i18n-allow: German user input under test
        "Was würdest du mir für den Umzug empfehlen?",  # i18n-allow: German user input under test
        # The "again"-shaped re-ask the maintainer quoted in the recall audit.
        "Wie hieß das Restaurant nochmal?",  # i18n-allow: German user input under test
        "Was ist nochmal bei dem Projekt passiert?",  # i18n-allow: German user input under test
    ],
)
def test_personal_questions_consult_with_the_standard_bar(utterance: str) -> None:
    verdict = should_consult_memory(utterance)
    assert verdict.consult is True
    assert verdict.strict is False


def test_fold_text_matches_the_prefolded_vocabulary_convention() -> None:
    """Umlauts become digraphs (ae/oe/ue), sharp-s becomes ss, Spanish
    accents strip — the same convention ``turn_planner._normalize`` uses."""
    assert fold_text("Wofür") == "wofuer"  # i18n-allow: German input under test
    assert fold_text("erzähl") == "erzaehl"  # i18n-allow: German input under test
    assert fold_text("heißt") == "heisst"  # i18n-allow: German input under test
    assert fold_text("¿Qué?") == "¿que?"  # i18n-allow: Spanish input under test


@pytest.mark.parametrize(
    "utterance",
    [
        # German pronominal adverbs — a question shape with no one-word English
        # equivalent. Regression: the live vault check on 2026-07-25 found these
        # rejected as "no lookup shape", silently muting real memory questions.
        "Woran arbeite ich gerade bei Personal Jarvis?",
        "Worauf habe ich mich letzte Woche vorbereitet?",
        "Womit hat mein Projekt angefangen?",
        "Wofuer brauche ich meinen Zweitrechner?",
    ],
)
def test_german_pronominal_adverbs_are_lookup_shapes(utterance: str) -> None:
    assert should_consult_memory(utterance).consult is True


@pytest.mark.parametrize(
    "utterance",
    ["Hallo", "ok", "danke dir", "", "   "],  # i18n-allow: German user input under test
)
def test_smalltalk_and_fragments_skip(utterance: str) -> None:
    assert should_consult_memory(utterance).consult is False


def test_action_requests_probe_only_behind_the_strict_bar() -> None:
    """An imperative may search (retrieval is cheap) but never injects
    loosely — nothing in the vault covers "turn on the light" strongly."""
    turn_on_the_light_de = "Mach das Licht an"  # i18n-allow: German input under test
    for utterance in (turn_on_the_light_de, "Turn on the kitchen light"):
        verdict = should_consult_memory(utterance)
        assert verdict.consult is True
        assert verdict.strict is True
        assert verdict.reason == "no_anchor_probe"


def test_statements_probe_with_the_strict_bar() -> None:
    """A statement mentioning a vault entity earns a strict-bar look — the
    old gate dropped it unsearched ("no_personal_anchor")."""
    bruno_birthday_de = "Bruno hat morgen Geburtstag"  # i18n-allow: German input under test
    verdict = should_consult_memory(bruno_birthday_de)
    assert verdict.consult is True
    assert verdict.strict is True


def test_gate_never_raises_on_odd_input() -> None:
    for weird in ["???", "\n\n", "🚗🚗🚗", "a b", "1 2 3 4 5"]:
        assert should_consult_memory(weird).consult in (True, False)


# ---------------------------------------------------------------------------
# Post-retrieval filter — a shared common word is not relevance
# ---------------------------------------------------------------------------


def test_hit_sharing_only_one_common_word_is_dropped() -> None:
    """The keyword index matches on ANY term; coverage is what filters."""
    hits = [
        FakeHit(
            title="Demo dinner",
            snippet="dinner with Example Contact in Example City",
            score=0.9,
        ),
        FakeHit(title="Car collection", snippet="thoughts about the world of engines", score=0.8),
    ]
    kept = relevant_hits(hits, "dinner Example Contact City")
    assert [hit.title for hit in kept] == ["Demo dinner"]


def test_weak_hit_below_the_relative_floor_is_dropped() -> None:
    strong = FakeHit(title="Example dinner", snippet="dinner with Example Contact", score=0.9)
    weak = FakeHit(title="Example dinner note", snippet="dinner with Example Contact", score=0.01)
    kept = relevant_hits([strong, weak], "dinner Example Contact")
    assert kept == [strong]


def test_relative_floor_never_uses_an_absolute_cutoff() -> None:
    """Scores are only comparable within one call — a uniformly low-scoring
    call must still return its hits rather than being wiped by a fixed floor."""
    hits = [
        FakeHit(title="Example dinner", snippet="dinner with Example Contact", score=0.05),
        FakeHit(title="Example dinner two", snippet="dinner with Example Contact", score=0.04),
    ]
    assert len(relevant_hits(hits, "dinner Example Contact")) == 2


def test_empty_hits_and_termless_queries_are_safe() -> None:
    assert relevant_hits([], "anything") == []
    hits = [FakeHit(title="t", snippet="s", score=1.0)]
    assert relevant_hits(hits, "?? ..") == hits


def test_filter_tolerates_hits_missing_attributes() -> None:
    class Bare:
        pass

    assert relevant_hits([Bare()], "dinner Example Contact") == []


def test_content_terms_folds_deduplicates_and_drops_stopwords() -> None:
    """Function words and generic question verbs leave the coverage
    denominator; only page-pointing terms remain."""
    mixed_case_de = "Über über ÜBER Reise"  # i18n-allow: German input under test
    assert content_terms(mixed_case_de) == ("reise",)
    assert "?" not in "".join(content_terms("Wann? Wo?"))  # i18n-allow: German input
    # "wie hiess mein Zahnarzt nochmal" must be judged on "zahnarzt" alone.
    dentist_de = "Wie hieß mein Zahnarzt nochmal?"  # i18n-allow: German input under test
    assert content_terms(dentist_de) == ("zahnarzt",)
    assert content_terms("What was the dentist called again?") == ("dentist",)


def test_strict_coverage_bar_requires_near_full_coverage() -> None:
    """The retrieval-first replacement for refusing to search: a page sharing
    one word with a three-term world question stays out under the strict bar,
    while a page that genuinely covers the question gets in."""
    berlin_travel = FakeHit(
        title="Berlin travel notes",
        snippet="Ideas for the next Berlin trip",
        score=0.9,
    )
    query = "Berlin Wetter morgen"  # i18n-allow: German query under test
    assert (
        relevant_hits([berlin_travel], query, min_coverage=DEFAULT_STRICT_MIN_COVERAGE)
        == []
    )
    bruno_page = FakeHit(
        title="Bruno", snippet="Bruno is the user's climbing partner.", score=0.9
    )
    assert relevant_hits(
        [bruno_page], "Bruno", min_coverage=DEFAULT_STRICT_MIN_COVERAGE
    ) == [bruno_page]


# ---------------------------------------------------------------------------
# Framing — the block must grant permission to ignore itself
# ---------------------------------------------------------------------------


def test_framed_block_tells_the_model_it_may_be_irrelevant() -> None:
    block = frame_context_block(["**Cars**: six of them"])
    assert "**Cars**: six of them" in block
    lowered = block.lower()
    assert "ignore it completely" in lowered
    assert "may have nothing to do" in lowered


def test_framed_block_forbids_redundant_knowledge_tool_calls() -> None:
    """The other half of the contract: when a note already answers, the
    model must not burn tool rounds re-finding the same fact (live trace
    2026-07-26: a dentist question spent ~10 rounds with the answer already
    injected)."""
    lowered = frame_context_block(["**Dentist**: 15 April, 14:00"]).lower()
    assert "do not call a knowledge-search tool" in lowered
    assert "answer directly" in lowered


def test_framing_an_empty_list_yields_nothing_to_append() -> None:
    assert frame_context_block([]) == ""
