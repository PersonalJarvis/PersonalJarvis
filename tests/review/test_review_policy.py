"""Tests fuer ReviewPolicy-Klassifikation (Phase 8.4).

Plan-Referenz: §AD-6 (selektive Aktivierung). Klassifikations-Matrix
mit 10+ Test-Queries pro Erwartung; Pass-Rate 100% auf den Cases.
"""
from __future__ import annotations

import pytest

from jarvis.core.review.policy import PolicyDecision, ReviewPolicy

# ----------------------------------------------------------------------
# Klassifikations-Matrix
# ----------------------------------------------------------------------

# Tasks die als "review" klassifiziert werden müssen (Aktions-Keywords,
# user-irreversibel oder Multi-Schritt-Synthese).
SHOULD_REVIEW_CASES: list[tuple[str, str]] = [
    (
        "schreibe ein Python-Script in scripts/foo.py das alle .md-Files umbenennt",
        "schreib + Datei-Schreib-Aktion",
    ),
    (
        "erstelle einen Skill der Spotify pausiert wenn ich rede",
        "erstelle + skill",
    ),
    (
        "generiere mir einen FastAPI-Endpoint mit Pydantic-Validation",
        "generier + code-spezifisch",
    ),
    (
        "refactor jarvis/core/protocols.py — extract HarnessTask in eigenes Modul",
        "refactor + code-edit",
    ),
    (
        "schreib mir Tests für die neue ReviewPipeline mit pytest",
        "schreib + script",
    ),
    (
        "modifizier die TTS-Provider-Config — wechsle auf gemini-flash und teste die Latenz",
        "modifizier + datei-mutation",
    ),
    (
        "erstelle einen Workflow der jeden Morgen meine Mails triagiert",
        "erstelle + skill-äquivalent",
    ),
    (
        "schreib eine SKILL.md für einen Excalidraw-Diagram-Skill",
        "schreib + skill",
    ),
]

# Tasks die NICHT reviewt werden (Smalltalk, Trivial-Aufrufe, Konversation).
SHOULD_NOT_REVIEW_CASES: list[tuple[str, str]] = [
    ("hi", "smalltalk + zu kurz"),
    ("hallo Jarvis", "smalltalk pattern"),
    ("danke", "thanks pattern"),
    ("was ist die hauptstadt von frankreich", "wissensfrage smalltalk"),
    ("wer ist der bundespräsident", "smalltalk pattern"),
    ("wie spät ist es", "wie spät pattern"),
    ("kurz", "<30 chars"),
    ("erzähl einen Witz über Programmierer", "konversation, kein review-keyword"),
    ("wie geht's dir heute", "smalltalk, kein review-keyword"),
    ("welcher Tag ist heute", "smalltalk wissensfrage"),
]


@pytest.mark.parametrize(
    "task,reason",
    SHOULD_REVIEW_CASES,
    ids=[c[1] for c in SHOULD_REVIEW_CASES],
)
def test_classifier_says_review(task: str, reason: str) -> None:
    policy = ReviewPolicy()
    decision = policy.should_review(task)
    assert isinstance(decision, PolicyDecision)
    assert decision.should_review is True, (
        f"expected review for: {task!r} (reason: {reason}); got {decision}"
    )
    assert decision.reason  # non-empty


@pytest.mark.parametrize(
    "task,reason",
    SHOULD_NOT_REVIEW_CASES,
    ids=[c[1] for c in SHOULD_NOT_REVIEW_CASES],
)
def test_classifier_says_no_review(task: str, reason: str) -> None:
    policy = ReviewPolicy()
    decision = policy.should_review(task)
    assert isinstance(decision, PolicyDecision)
    assert decision.should_review is False, (
        f"expected NO review for: {task!r} (reason: {reason}); got {decision}"
    )


# ----------------------------------------------------------------------
# Edge Cases
# ----------------------------------------------------------------------


def test_empty_task_no_review() -> None:
    policy = ReviewPolicy()
    decision = policy.should_review("")
    assert decision.should_review is False
    assert "too short" in decision.reason


def test_whitespace_only_no_review() -> None:
    policy = ReviewPolicy()
    decision = policy.should_review("   \n\t  ")
    assert decision.should_review is False


def test_smalltalk_beats_action_keyword() -> None:
    """Tasks mit Smalltalk-Pattern UND Action-Keyword werden als
    Smalltalk klassifiziert (Smalltalk-Allowlist gewinnt — Plan-§AD-6
    Persona-Mandat: Smalltalk-Allowlist ist der erste Cut).
    """
    policy = ReviewPolicy()
    decision = policy.should_review(
        "danke, schreib mir kurz nen Witz"  # 'schreib' + 'danke'
    )
    assert decision.should_review is False
    assert "smalltalk" in decision.reason.lower()


def test_word_boundary_does_not_match_substring() -> None:
    """`code` als Substring in `decode` matcht NICHT — word boundary."""
    policy = ReviewPolicy()
    decision = policy.should_review(
        "ich kann den decoder ausgabewert nicht interpretieren bitte hilf mir mal damit"
    )
    # 'decode' soll nicht als 'code' triggern
    assert decision.should_review is False, decision


def test_default_no_review_without_keywords() -> None:
    """Plan-§AD-6: Default ist konservativ — ohne Match → kein Review."""
    policy = ReviewPolicy()
    decision = policy.should_review(
        "Was war die Antwort auf meine letzte Frage von gestern Abend?"
    )
    assert decision.should_review is False


def test_decision_reason_is_specific() -> None:
    policy = ReviewPolicy()
    d_short = policy.should_review("kurz")
    assert "too short" in d_short.reason.lower()

    d_smalltalk = policy.should_review("hallo, was geht so heute morgen mein freund?")
    assert "smalltalk" in d_smalltalk.reason.lower()

    d_action = policy.should_review(
        "schreibe ein Python-Module das die Datenbank migriert"
    )
    assert "action" in d_action.reason.lower()


def test_custom_keywords_override() -> None:
    """Caller kann eigene Keyword-Listen mitgeben (Phase 8.4-API)."""
    policy = ReviewPolicy(
        review_keywords=("nuke", "bomb"),
        noreview_keywords=("foo",),
        min_length=5,
    )
    assert policy.should_review("nuke the system gracefully").should_review is True
    assert policy.should_review("foo bar baz").should_review is False
