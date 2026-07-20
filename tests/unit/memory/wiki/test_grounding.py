"""Guard tests for the deterministic evidence-tier classifier.

The classifier is the deterministic floor under the Stage-1 prompt rules:
it decides whether an attitude/habit claim about the user is grounded
``explicit`` (literal assertion), ``behavioral`` (first-person
lived-experience report), or not at all (``None`` — blocked).  Topic
questions must NEVER ground a personal claim, in any supported language.
"""
from __future__ import annotations

import pytest

from jarvis.memory.wiki.grounding import (
    classify_user_attitude_evidence,
    is_unsupported_user_interest_claim,
)

_USER = "ruben"


def _classify(fact: str, evidence: str, subjects: tuple[str, ...] = (_USER, "golf")):
    return classify_user_attitude_evidence(
        fact=fact,
        subjects=subjects,
        evidence_excerpt=f"Evidence user turn [turn-1]: {evidence}",
        user_slug=_USER,
    )


def test_explicit_interest_assertion_classifies_explicit() -> None:
    assert _classify(
        "The user loves golf.",
        "I love being out on golf courses with my buddies, "
        "playing this sport actively.",
    ) == "explicit"


def test_habitual_activity_without_interest_verb_classifies_behavioral() -> None:
    assert _classify(
        "The user plays golf regularly with friends.",
        "I was out on the golf course again on Saturday with my buddies.",
    ) == "behavioral"


def test_enjoyment_claim_from_lived_experience_classifies_behavioral() -> None:
    # The fact claims enjoyment; the evidence reports doing, not liking.
    assert _classify(
        "The user enjoys golf.",
        "Every Saturday I meet my buddies out on the golf course.",
    ) == "behavioral"


def test_topic_question_stays_blocked() -> None:
    assert _classify(
        "The user is interested in Monaco.",
        "Tell me about Monaco.",
        subjects=(_USER, "monaco"),
    ) is None
    assert is_unsupported_user_interest_claim(
        fact="The user is interested in Monaco.",
        subjects=(_USER, "monaco"),
        evidence_excerpt="Evidence user turn [turn-1]: Tell me about Monaco.",
        user_slug=_USER,
    )


def test_question_clause_never_grounds_behavioral() -> None:
    assert _classify(
        "The user plays golf regularly.",
        "Do I play golf on Saturdays?",
    ) is None


def test_third_party_activity_is_not_a_user_claim() -> None:
    # A fact about another person never activates the user-attitude guard.
    assert _classify(
        "Lena plays golf regularly.",
        "My friend Lena plays golf every weekend.",
        subjects=("lena", "golf"),
    ) == "explicit"


def test_negative_assertion_polarity_unchanged() -> None:
    assert _classify(
        "The user dislikes golf.",
        "I hate golf.",
    ) == "explicit"
    # A positive-experience report cannot ground a negative attitude claim.
    assert _classify(
        "The user dislikes golf.",
        "I was out on the golf course again on Saturday.",
    ) is None


def test_non_attitude_fact_families_stay_untouched() -> None:
    assert _classify(
        "The user owns a yacht named Aurora.",
        "I own a yacht named Aurora.",
        subjects=(_USER, "aurora"),
    ) == "explicit"


@pytest.mark.parametrize(
    "evidence",
    [
        "Ich spiele jeden Samstag Golf mit meinen Freunden.",  # i18n-allow: input vocabulary under test
        "Ich bin sehr gerne draussen auf Golfplaetzen unterwegs.",  # i18n-allow: input vocabulary under test
        "Juego al golf cada sabado con mis amigos.",
    ],
)
def test_multilingual_behavioral_reports_classify_behavioral(evidence: str) -> None:
    assert _classify(
        "The user plays golf regularly with friends.",
        evidence,
    ) == "behavioral"


def test_multilingual_topic_question_stays_blocked() -> None:
    assert _classify(
        "The user is interested in Monaco.",
        "Erzaehl mir was ueber Monaco?",  # i18n-allow: input vocabulary under test
        subjects=(_USER, "monaco"),
    ) is None
