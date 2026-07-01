"""Tests for the ReviewVerdict Pydantic models (Phase 8.1).

Plan reference: §6.1 acceptance criterion 1 — JSON↔Pydantic roundtrip,
status-enum rejection on invalid values.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from jarvis.core.review.verdict import (
    ReviewIssue,
    ReviewStatus,
    ReviewVerdict,
    RubricResult,
)

# ----------------------------------------------------------------------
# ReviewStatus enum
# ----------------------------------------------------------------------


def test_review_status_values() -> None:
    """Plan §AD-3: exactly three lowercase string values."""
    assert ReviewStatus.PASS.value == "pass"
    assert ReviewStatus.NEEDS_REVISION.value == "needs_revision"
    assert ReviewStatus.FAIL.value == "fail"
    assert {s.value for s in ReviewStatus} == {"pass", "needs_revision", "fail"}


def test_review_status_str_subclass() -> None:
    """ReviewStatus is `str, Enum` — comparison with strings works."""
    assert ReviewStatus.PASS == "pass"  # noqa: S105 — status value
    assert ReviewStatus("pass") is ReviewStatus.PASS


# ----------------------------------------------------------------------
# Happy-Path Roundtrip
# ----------------------------------------------------------------------


def _full_verdict_dict() -> dict:
    return {
        "status": "needs_revision",
        "summary": "Worker has lingering TODO and missed one rubric item.",
        "issues": [
            {
                "severity": "warning",
                "description": "TODO marker in produced file",
                "location": "scripts/foo.py:42",
                "fix_hint": "Implement the rename loop or remove TODO.",
            },
            {
                "severity": "suggestion",
                "description": "Could use pathlib instead of os.path",
                "location": None,
                "fix_hint": None,
            },
        ],
        "rubric_results": [
            {"name": "task_completion", "passed": False, "note": "TODO left"},
            {"name": "voice_friendliness", "passed": True, "note": None},
        ],
        "score": 0.55,
    }


def test_review_verdict_roundtrip_full() -> None:
    """Pydantic <-> JSON roundtrip with a fully populated verdict."""
    payload = _full_verdict_dict()
    verdict = ReviewVerdict.model_validate(payload)
    assert verdict.status is ReviewStatus.NEEDS_REVISION
    assert len(verdict.issues) == 2
    assert verdict.issues[0].severity == "warning"
    assert verdict.issues[0].location == "scripts/foo.py:42"
    assert verdict.score == 0.55

    # JSON roundtrip: dump -> load -> dump must be idempotent
    serialized = verdict.model_dump(mode="json")
    reparsed = ReviewVerdict.model_validate(serialized)
    assert reparsed.model_dump(mode="json") == serialized


def test_review_verdict_minimal() -> None:
    """Default factories: issues/rubric_results are empty lists."""
    verdict = ReviewVerdict(
        status=ReviewStatus.PASS,
        summary="All good.",
        score=1.0,
    )
    assert verdict.issues == []
    assert verdict.rubric_results == []


# ----------------------------------------------------------------------
# Status-Enum-Reject
# ----------------------------------------------------------------------


def test_invalid_status_rejected() -> None:
    """Plan §6.1 AC: rejects invalid status strings."""
    payload = _full_verdict_dict()
    payload["status"] = "approved"  # not in the enum
    with pytest.raises(ValidationError) as exc:
        ReviewVerdict.model_validate(payload)
    assert "status" in str(exc.value).lower()


@pytest.mark.parametrize("bad", ["", "PASS", "passed", "ok", "needs revision"])
def test_invalid_status_variants_rejected(bad: str) -> None:
    payload = _full_verdict_dict()
    payload["status"] = bad
    with pytest.raises(ValidationError):
        ReviewVerdict.model_validate(payload)


# ----------------------------------------------------------------------
# Score-Reject
# ----------------------------------------------------------------------


@pytest.mark.parametrize("bad_score", [-0.01, 1.01, 1.5, 2.0, -1.0])
def test_score_out_of_range_rejected(bad_score: float) -> None:
    """Plan §6.1: score must lie in [0.0, 1.0]."""
    payload = _full_verdict_dict()
    payload["score"] = bad_score
    with pytest.raises(ValidationError) as exc:
        ReviewVerdict.model_validate(payload)
    assert "score" in str(exc.value).lower()


@pytest.mark.parametrize("good_score", [0.0, 0.5, 1.0])
def test_score_boundary_values_accepted(good_score: float) -> None:
    """Boundary values 0.0 and 1.0 are inclusively allowed."""
    payload = _full_verdict_dict()
    payload["score"] = good_score
    verdict = ReviewVerdict.model_validate(payload)
    assert verdict.score == good_score


def test_score_required() -> None:
    """score is required (no default)."""
    payload = _full_verdict_dict()
    del payload["score"]
    with pytest.raises(ValidationError):
        ReviewVerdict.model_validate(payload)


# ----------------------------------------------------------------------
# Severity-Reject
# ----------------------------------------------------------------------


@pytest.mark.parametrize("bad_severity", ["info", "error", "fatal", "", "WARNING"])
def test_invalid_severity_rejected(bad_severity: str) -> None:
    """Severity is Literal['critical','warning','suggestion']."""
    with pytest.raises(ValidationError) as exc:
        ReviewIssue(severity=bad_severity, description="x")  # type: ignore[arg-type]
    assert "severity" in str(exc.value).lower()


def test_valid_severities() -> None:
    for sev in ("critical", "warning", "suggestion"):
        issue = ReviewIssue(severity=sev, description="x")  # type: ignore[arg-type]
        assert issue.severity == sev


# ----------------------------------------------------------------------
# Summary maxLength
# ----------------------------------------------------------------------


def test_summary_max_length_enforced() -> None:
    """Plan §9.2: summary maxLength=200 — voice suitability."""
    payload = _full_verdict_dict()
    payload["summary"] = "x" * 201
    with pytest.raises(ValidationError) as exc:
        ReviewVerdict.model_validate(payload)
    assert "summary" in str(exc.value).lower()


def test_summary_at_limit_accepted() -> None:
    payload = _full_verdict_dict()
    payload["summary"] = "x" * 200
    verdict = ReviewVerdict.model_validate(payload)
    assert len(verdict.summary) == 200


# ----------------------------------------------------------------------
# Extra-Fields-Reject (extra="forbid")
# ----------------------------------------------------------------------


def test_extra_field_in_verdict_rejected() -> None:
    payload = _full_verdict_dict()
    payload["bonus"] = "not allowed"
    with pytest.raises(ValidationError):
        ReviewVerdict.model_validate(payload)


def test_extra_field_in_issue_rejected() -> None:
    payload = _full_verdict_dict()
    payload["issues"][0]["bonus"] = "not allowed"
    with pytest.raises(ValidationError):
        ReviewVerdict.model_validate(payload)


# ----------------------------------------------------------------------
# RubricResult
# ----------------------------------------------------------------------


def test_rubric_result_minimal() -> None:
    rr = RubricResult(name="task_completion", passed=True)
    assert rr.note is None


def test_rubric_result_passed_required_bool() -> None:
    with pytest.raises(ValidationError):
        RubricResult(name="x")  # type: ignore[call-arg]
