"""Tests for ReviewPolicy classification (Phase 8.4).

Plan reference: §AD-6 (selective activation). Classification matrix
with 10+ test queries per expectation; 100% pass rate on the cases.
"""
from __future__ import annotations

import pytest

from jarvis.core.review.policy import PolicyDecision, ReviewPolicy

# ----------------------------------------------------------------------
# Classification matrix
# ----------------------------------------------------------------------

# Tasks that must be classified as "review" (action keywords,
# user-irreversible, or multi-step synthesis).
# NOTE: the task strings below are simulated German user utterances —
# they are the content under test for ReviewPolicy's German keyword
# matcher (jarvis/core/review/policy.py) and must stay German.
SHOULD_REVIEW_CASES: list[tuple[str, str]] = [
    (
        "schreibe ein Python-Script in scripts/foo.py das alle .md-Files umbenennt",  # i18n-allow
        "write + file-write action",
    ),
    (
        "erstelle einen Skill der Spotify pausiert wenn ich rede",  # i18n-allow
        "create + skill",
    ),
    (
        "generiere mir einen FastAPI-Endpoint mit Pydantic-Validation",  # i18n-allow
        "generate + code-specific",
    ),
    (
        "refactor jarvis/core/protocols.py — extract HarnessTask in eigenes Modul",  # i18n-allow
        "refactor + code-edit",
    ),
    (
        "schreib mir Tests für die neue ReviewPipeline mit pytest",  # i18n-allow
        "write + script",
    ),
    (
        "modifizier die TTS-Provider-Config — wechsle auf gemini-flash und teste die Latenz",  # i18n-allow
        "modify + file mutation",
    ),
    (
        "erstelle einen Workflow der jeden Morgen meine Mails triagiert",  # i18n-allow
        "create + skill-equivalent",
    ),
    (
        "schreib eine SKILL.md für einen Excalidraw-Diagram-Skill",  # i18n-allow
        "write + skill",
    ),
]

# Tasks that must NOT be reviewed (smalltalk, trivial calls, conversation).
SHOULD_NOT_REVIEW_CASES: list[tuple[str, str]] = [
    ("hi", "smalltalk + too short"),
    ("hallo Jarvis", "smalltalk pattern"),  # i18n-allow
    ("danke", "thanks pattern"),  # i18n-allow
    ("was ist die hauptstadt von frankreich", "knowledge-question smalltalk"),  # i18n-allow
    ("wer ist der bundespräsident", "smalltalk pattern"),  # i18n-allow
    ("wie spät ist es", "'wie spät' keyword pattern"),  # i18n-allow
    ("short", "<30 chars"),
    ("erzähl einen Witz über Programmierer", "conversation, no review keyword"),  # i18n-allow
    ("wie geht's dir heute", "smalltalk, no review keyword"),  # i18n-allow
    ("welcher Tag ist heute", "smalltalk knowledge-question"),  # i18n-allow
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
    """Tasks with a smalltalk pattern AND an action keyword are classified
    as smalltalk (the smalltalk allowlist wins — plan §AD-6 persona
    mandate: the smalltalk allowlist is the first cut).
    """
    policy = ReviewPolicy()
    decision = policy.should_review(
        "danke, schreib mir kurz nen Witz"  # 'schreib' + 'danke'
    )
    assert decision.should_review is False
    assert "smalltalk" in decision.reason.lower()


def test_word_boundary_does_not_match_substring() -> None:
    """`code` as a substring of `decode` does NOT match — word boundary."""
    policy = ReviewPolicy()
    decision = policy.should_review(
        "ich kann den decoder ausgabewert nicht interpretieren bitte hilf mir mal damit"  # i18n-allow
    )
    # 'decode' should not trigger as 'code'
    assert decision.should_review is False, decision


def test_default_no_review_without_keywords() -> None:
    """Plan §AD-6: default is conservative — no match → no review."""
    policy = ReviewPolicy()
    decision = policy.should_review(
        "Was war die Antwort auf meine letzte Frage von gestern Abend?"  # i18n-allow
    )
    assert decision.should_review is False


def test_decision_reason_is_specific() -> None:
    policy = ReviewPolicy()
    d_short = policy.should_review("kurz")
    assert "too short" in d_short.reason.lower()

    d_smalltalk = policy.should_review("hallo, was geht so heute morgen mein freund?")
    assert "smalltalk" in d_smalltalk.reason.lower()

    d_action = policy.should_review(
        "schreibe ein Python-Module das die Datenbank migriert"  # i18n-allow
    )
    assert "action" in d_action.reason.lower()


def test_custom_keywords_override() -> None:
    """Caller can supply custom keyword lists (Phase 8.4 API)."""
    policy = ReviewPolicy(
        review_keywords=("nuke", "bomb"),
        noreview_keywords=("foo",),
        min_length=5,
    )
    assert policy.should_review("nuke the system gracefully").should_review is True
    assert policy.should_review("foo bar baz").should_review is False
