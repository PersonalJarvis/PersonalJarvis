"""Evidence-gate enforcement (live repro 2026-06-17, session 296abc82).

"Was ist in meiner Google Cloud Console gerade los?" — the gate mandated
`cli_gcloud` (require_tool), but the deep model answered WITHOUT calling it
(executed_tool_names empty) and CONFABULATED a reason ("the gcloud tool blocked
execution because it classified the request as an explanatory question"). A
mandated-tool turn whose tool never ran must never speak the model's
(unverified) answer.
"""
from jarvis.brain.manager import (
    _answer_claims_unverified_data,
    _evidence_answer_is_unverified,
    _evidence_unfulfilled_answer,
    _unfulfilled_replacement,
)


def test_unverified_when_mandated_tool_not_executed():
    assert _evidence_answer_is_unverified(
        "cli_gcloud", set(), "the gcloud tool blocked execution", suppressed=False
    ) is True


def test_verified_when_mandated_tool_was_executed():
    assert _evidence_answer_is_unverified(
        "cli_gcloud", {"cli_gcloud"}, "Your projects: foo, bar", suppressed=False
    ) is False


def test_not_unverified_when_no_tool_mandated():
    # A normal turn (gate PASSed / no CLI) is never touched.
    assert _evidence_answer_is_unverified(
        "", set(), "any free answer", suppressed=False
    ) is False


def test_not_unverified_when_suppressed():
    # Fire-and-forget spawn (suppress_response) is not a data answer.
    assert _evidence_answer_is_unverified(
        "cli_gcloud", set(), "", suppressed=True
    ) is False


def test_not_unverified_when_response_empty():
    # Empty response is handled by the empty-response guard, not here.
    assert _evidence_answer_is_unverified(
        "cli_gcloud", set(), "   ", suppressed=False
    ) is False


def test_unfulfilled_answer_is_honest_and_localized():
    de = _evidence_unfulfilled_answer(lang="de")
    en = _evidence_unfulfilled_answer(lang="en")
    es = _evidence_unfulfilled_answer(lang="es")
    assert "abrufen" in de.lower() or "durchgelaufen" in de.lower()
    assert "retrieve" in en.lower() or "go through" in en.lower()
    # Spanish is a first-class supported language (Runtime Output Language).
    assert "herramienta" in es.lower() or "pude" in es.lower()
    # Never claims a tool "blocked" execution or invents a classification reason.
    assert "blockiert" not in de.lower() and "erkl" not in de.lower()
    assert "block" not in en.lower() and "classif" not in en.lower()


def test_unfulfilled_answer_unknown_language_falls_back_to_default():
    # An unrecognised code must degrade safely, never crash the spoken turn.
    fallback = _evidence_unfulfilled_answer(lang="fr")
    assert isinstance(fallback, str) and fallback.strip()


# ---------------------------------------------------------------------------
# The backstop must not delete a correct answer (audit GT-18).
#
# `_evidence_answer_is_unverified` is true for EVERY answer of a mandated-tool
# turn whose tool did not run — including an explanation and a fact the user
# supplied two turns ago. Replacing those made the guard fabricate a failure to
# prevent a fabrication: the model answered correctly, the user heard
# "Ich konnte das gerade nicht abrufen". Only a concrete data claim is  # i18n-allow
# replaced now.
# ---------------------------------------------------------------------------


def _read_replacement(answer: str) -> str | None:
    return _unfulfilled_replacement(
        required_tool="list_calendar_events",
        executed=set(),
        response_text=answer,
        suppressed=False,
        is_write=False,
        lang="en",
        domain="calendar",
    )


def test_general_knowledge_answer_is_kept():
    answer = (
        "A diesel engine ignites its fuel by compression heat, not by a spark "
        "plug."
    )
    assert _answer_claims_unverified_data(answer) is False
    assert _read_replacement(answer) is None


def test_answer_attributed_to_the_conversation_is_kept():
    # The user themselves said it — no tool could have grounded it, and the
    # honest fallback would be the wrong answer.
    answer = "You said earlier that the meeting is on Monday."
    assert _answer_claims_unverified_data(answer) is False
    assert _read_replacement(answer) is None


def test_clarifying_question_is_kept():
    answer = "Which period do you mean exactly?"
    assert _read_replacement(answer) is None


def test_concrete_data_claim_is_still_replaced():
    for answer in (
        "You have an appointment tomorrow at 9:15 with Ms Meier.",
        "You have no appointments tomorrow.",
        "Your next appointment is on Thursday.",
        "- Standup\n- Review",
        "You have 3 unread messages from support@example.com.",
    ):
        assert _answer_claims_unverified_data(answer) is True, answer
        assert _read_replacement(answer) is not None, answer


def test_write_mandate_is_unchanged_by_the_data_claim_test():
    # A write is a say-do gap, not a data claim: a flat confirmation is still
    # corrected and a clarifying question is still kept.
    flat = _unfulfilled_replacement(
        required_tool="contact-upsert", executed=set(),
        response_text="Okay, all done.", suppressed=False, is_write=True,
        lang="en",
    )
    question = _unfulfilled_replacement(
        required_tool="contact-upsert", executed=set(),
        response_text="What is the email address?", suppressed=False,
        is_write=True, lang="en",
    )
    assert flat is not None
    assert question is None


def test_confabulated_tool_behaviour_is_still_replaced():
    # The original live repro (2026-06-17, session 296abc82): the model invented
    # what the tool did. No figure, no date — and entirely made up.
    for answer in (
        "the gcloud tool blocked execution because it classified the request "
        "as an explanatory question",
        "Der Befehl ist fehlgeschlagen, deshalb habe ich nichts.",  # i18n-allow
        "The CLI returned an empty result.",
    ):
        assert _answer_claims_unverified_data(answer) is True, answer
        assert _read_replacement(answer) is not None, answer
