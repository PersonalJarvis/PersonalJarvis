"""Guards for the widened pre-turn consult gate (ambient personal knowledge).

The gate used to open for two turn classes only: explicit recollection and a
possessive lookup. A third class earns it — planning, recommendation and
decision turns ("what should I do", "what is the fastest way to X", "any ideas
for the weekend"), where the right answer depends on who is asking.

Both directions are guarded here, because a widened gate is only safe if it
stays shut where it was shut before: general-knowledge questions must still
never reach the memory, and smalltalk must still cost nothing.
"""

from __future__ import annotations

import pytest

from jarvis.brain.wiki_relevance import should_consult_memory

# ---------------------------------------------------------------------------
# Opens: planning / recommendation / decision turns
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "utterance",
    [
        # de (CLAUDE.md §1, closed-list item 4 — speech input under test)
        "Was soll ich heute Abend machen?",  # i18n-allow: German input under test
        "Wie komme ich am besten nach Berlin?",  # i18n-allow: German input under test
        "Hast du Ideen fuer das Wochenende?",  # i18n-allow: German input under test
        "Was ist der schnellste Weg zum Flughafen?",  # i18n-allow: German input under test
        "Lohnt sich das ueberhaupt noch?",  # i18n-allow: German input under test
        "Welche Option wuerdest du nehmen?",  # i18n-allow: German input under test
        "Was empfiehlst du mir dazu?",  # i18n-allow: German input under test
        "Hilf mir bei der Entscheidung zwischen A und B.",  # i18n-allow: German input
        # en
        "What should I do this evening?",
        "What is the fastest way to the airport?",
        "Any ideas for the weekend?",
        "What's the best way to learn this?",
        "Can you recommend something for tonight?",
        "Help me decide between the two offers.",
        "Is it worth switching now?",
        "Any tips for the trip next week?",
        # es (same closed-list item 4)
        "¿Qué debería hacer este fin de semana?",  # i18n-allow: Spanish input under test
        "¿Cuál es la mejor manera de llegar?",  # i18n-allow: Spanish input under test
        "¿Alguna idea para la cena?",  # i18n-allow: Spanish input under test
        "¿Qué me recomiendas para el viaje?",  # i18n-allow: Spanish input under test
        "¿Vale la pena cambiar ahora?",  # i18n-allow: Spanish input under test
    ],
)
def test_planning_turns_consult_memory(utterance: str) -> None:
    verdict = should_consult_memory(utterance)
    assert verdict.consult is True, utterance
    assert verdict.reason == "planning_advice", utterance


def test_planning_beats_the_general_knowledge_skip() -> None:
    """"What is the fastest way home" is BOTH shapes — advice must win.

    The general-knowledge skip is matched by the leading "what is"; without an
    explicit ordering rule the advice reading would be lost.
    """
    assert should_consult_memory("What is the best way to get home?").consult is True
    assert should_consult_memory("What is the fastest way to Berlin?").consult is True


# ---------------------------------------------------------------------------
# Stays strict: general knowledge, smalltalk, quiz superlatives
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "utterance",
    [
        "How tall is the Eiffel Tower?",
        "Wie hoch ist der Eiffelturm?",  # i18n-allow: German speech input under test
        "¿Qué es la torre más alta del mundo?",  # i18n-allow: Spanish speech input under test
        "What is the tallest tower in the world?",
        "How does a diesel engine work?",
        "Who was Ada Lovelace?",
        "Explain reciprocal rank fusion.",
        "What is the capital of Peru?",
        # The reason the planning vocabulary is compound rather than a bare
        # superlative: a quiz about the world still reads as world knowledge.
        "What is the fastest animal on earth?",
        "Wie funktioniert ein Dieselmotor?",  # i18n-allow: German speech input under test
    ],
)
def test_general_knowledge_gets_only_the_strict_probe(utterance: str) -> None:
    """Retrieval-first: these turns may search, but never with the standard
    bar — a vault page must cover nearly the whole question to ride along."""
    verdict = should_consult_memory(utterance)
    assert verdict.consult is True, utterance
    assert verdict.strict is True, utterance
    assert verdict.reason == "world_shape_probe", utterance


@pytest.mark.parametrize(
    "utterance",
    [
        "ok",
        "",
        "   ",
        "danke dir",  # i18n-allow: German smalltalk under test
    ],
)
def test_fragments_skip_entirely(utterance: str) -> None:
    assert should_consult_memory(utterance).consult is False, utterance


@pytest.mark.parametrize(
    "utterance",
    [
        "Hallo, wie geht es dir?",  # i18n-allow: German smalltalk under test
        "Guten Morgen zusammen",  # i18n-allow: German smalltalk under test
        "danke dir vielmals",  # i18n-allow: German smalltalk under test
        "Alles klar bei dir?",  # i18n-allow: German smalltalk under test
        "Hey there, good morning",
        "thanks a lot for that",
    ],
)
def test_smalltalk_gets_at_most_the_strict_probe(utterance: str) -> None:
    """Retrieval-first: sentence-length smalltalk may search the (free,
    local) vault, but only behind the strict bar — no greeting shares enough
    content terms with a page to inject anything."""
    verdict = should_consult_memory(utterance)
    assert verdict.consult is True, utterance
    assert verdict.strict is True, utterance


def test_the_older_turn_classes_are_untouched() -> None:
    """The widening must not renumber the reasons the gate already reported."""
    assert should_consult_memory("Do you remember where we stayed?").reason == (
        "recollection_phrase"
    )
    assert should_consult_memory("What are my billing rules?").reason == (
        "personal_lookup"
    )
    anchorless = should_consult_memory("Turn on the kitchen light")
    assert anchorless.reason == "no_anchor_probe"
    assert anchorless.strict is True


def test_widened_gate_never_raises_on_odd_input() -> None:
    for weird in ["???", "\n\n", "🚗🚗🚗", "a b", "1 2 3 4 5", "-" * 200]:
        assert should_consult_memory(weird).consult in (True, False)
