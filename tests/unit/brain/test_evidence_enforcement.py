"""Evidence-gate enforcement (live repro 2026-06-17, session 296abc82).

"Was ist in meiner Google Cloud Console gerade los?" — the gate mandated
`cli_gcloud` (require_tool), but the deep model answered WITHOUT calling it
(executed_tool_names empty) and CONFABULATED a reason ("the gcloud tool blocked
execution because it classified the request as an explanatory question"). A
mandated-tool turn whose tool never ran must never speak the model's
(unverified) answer.
"""
from jarvis.brain.manager import (
    _evidence_answer_is_unverified,
    _evidence_unfulfilled_answer,
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
