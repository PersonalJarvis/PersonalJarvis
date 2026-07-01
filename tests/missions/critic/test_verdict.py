"""Tests for the CriticVerdict schema, aggregation helper, schema export."""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from jarvis.missions.critic.verdict import (
    CRITIC_JSON_SCHEMA,
    LOW_CONFIDENCE_THRESHOLD,
    REQUIRED_AXES,
    CriticAxis,
    CriticIssue,
    CriticSchemaInvalid,
    CriticTimeout,
    CriticVerdict,
    CriticVerdictInconsistent,
    aggregate_axes_status,
    is_approval_valid,
    requires_escalation,
)


# --- Helper ---


def _make_verdict(
    *,
    verdict: str = "approve",
    all_axes_pass: bool = True,
    all_evidence_present: bool = True,
    confidence: float = 0.9,
    security_status: str | None = None,
    issues: list[CriticIssue] | None = None,
) -> CriticVerdict:
    """Verdict factory for compact tests."""
    axes = {}
    for ax in REQUIRED_AXES:
        status = "pass" if all_axes_pass else "fail"
        if ax == "security" and security_status is not None:
            status = security_status  # type: ignore[assignment]
        evidence = ["src/foo.py:42"] if all_evidence_present else []
        axes[ax] = CriticAxis(status=status, evidence=evidence)  # type: ignore[arg-type]
    return CriticVerdict(
        verdict=verdict,  # type: ignore[arg-type]
        axes=axes,
        issues=issues or [],
        correction_instruction="" if verdict == "approve" else "fix bug X",
        summary="ok" if verdict == "approve" else "needs fix",
        summary_de="ok" if verdict == "approve" else "muss korrigiert werden",  # i18n-allow (German value under summary_de field)
        confidence=confidence,
        suggested_next_action="accept" if verdict == "approve" else "retry",
    )


# --- Roundtrip ---


def test_verdict_roundtrip_serializes_identically() -> None:
    v = _make_verdict()
    raw = v.model_dump_json()
    v2 = CriticVerdict.model_validate_json(raw)
    assert v == v2


def test_verdict_extra_fields_rejected() -> None:
    raw = {
        "verdict": "approve",
        "axes": {
            ax: {"status": "pass", "evidence": ["x"]} for ax in REQUIRED_AXES
        },
        "issues": [],
        "correction_instruction": "",
        "summary": "ok",
        "summary_de": "ok",
        "confidence": 0.9,
        "suggested_next_action": "accept",
        "extra_field": "should be rejected",
    }
    with pytest.raises(ValidationError):
        CriticVerdict.model_validate(raw)


def test_axis_extra_fields_rejected() -> None:
    raw = {"status": "pass", "evidence": [], "extra": "no"}
    with pytest.raises(ValidationError):
        CriticAxis.model_validate(raw)


def test_verdict_is_frozen() -> None:
    v = _make_verdict()
    with pytest.raises(ValidationError):
        v.verdict = "reject"  # type: ignore[misc]


def test_confidence_out_of_range_rejected() -> None:
    with pytest.raises(ValidationError):
        _make_verdict(confidence=1.5)
    with pytest.raises(ValidationError):
        _make_verdict(confidence=-0.1)


def test_summary_max_length_enforced() -> None:
    too_long = "x" * 281
    with pytest.raises(ValidationError):
        CriticVerdict(
            verdict="approve",
            axes={
                ax: CriticAxis(status="pass", evidence=["e"])  # type: ignore[arg-type]
                for ax in REQUIRED_AXES
            },
            summary=too_long,
            confidence=0.9,
            suggested_next_action="accept",
        )


# --- aggregate_axes_status ---


def test_aggregate_all_pass_returns_pass() -> None:
    v = _make_verdict(all_axes_pass=True)
    assert aggregate_axes_status(v) == "pass"


def test_aggregate_one_fail_returns_fail() -> None:
    v = _make_verdict(all_axes_pass=True, security_status="fail")
    assert aggregate_axes_status(v) == "fail"


def test_aggregate_missing_axis_returns_fail() -> None:
    v = CriticVerdict(
        verdict="approve",
        axes={
            "correctness": CriticAxis(status="pass", evidence=["x"]),
            "completeness": CriticAxis(status="pass", evidence=["x"]),
            # security + side_effects fehlen absichtlich
        },
        confidence=0.9,
        suggested_next_action="accept",
    )
    assert aggregate_axes_status(v) == "fail"


# --- is_approval_valid (Anti-Sycophancy / Anti-Empty-Evidence) ---


def test_approval_valid_when_all_pass_with_evidence() -> None:
    v = _make_verdict(all_axes_pass=True, all_evidence_present=True)
    assert is_approval_valid(v) is True


def test_approval_invalid_when_revise_verdict() -> None:
    v = _make_verdict(verdict="revise", all_axes_pass=True)
    assert is_approval_valid(v) is False


def test_approval_invalid_when_one_axis_fails() -> None:
    v = _make_verdict(all_axes_pass=True, security_status="fail")
    assert is_approval_valid(v) is False


def test_approval_invalid_when_evidence_empty() -> None:
    """An empty-evidence approval is an abstention — Criterion 1 design-reviewer."""
    v = _make_verdict(all_axes_pass=True, all_evidence_present=False)
    assert is_approval_valid(v) is False


def test_approval_invalid_when_one_axis_evidence_empty() -> None:
    """A single axis without evidence is enough to reject."""
    v = _make_verdict(all_axes_pass=True, all_evidence_present=True)
    bad_axes = dict(v.axes)
    bad_axes["completeness"] = CriticAxis(status="pass", evidence=[])
    v2 = CriticVerdict(
        verdict="approve",
        axes=bad_axes,
        confidence=0.9,
        suggested_next_action="accept",
    )
    assert is_approval_valid(v2) is False


# --- requires_escalation ---


def test_escalation_low_confidence_triggers() -> None:
    v = _make_verdict(confidence=LOW_CONFIDENCE_THRESHOLD - 0.01)
    assert requires_escalation(v) is True


def test_escalation_at_threshold_no_trigger() -> None:
    v = _make_verdict(confidence=LOW_CONFIDENCE_THRESHOLD)
    assert requires_escalation(v) is False


def test_escalation_security_fail_triggers() -> None:
    v = _make_verdict(security_status="fail")
    assert requires_escalation(v) is True


def test_escalation_critical_security_issue_triggers() -> None:
    issue = CriticIssue(
        severity="critical",
        category="security",
        description="hardcoded secret",
        evidence_ref="src/x.py:1",
        fix="remove secret",
    )
    v = _make_verdict(issues=[issue])
    assert requires_escalation(v) is True


def test_escalation_low_severity_security_issue_not_triggered() -> None:
    issue = CriticIssue(
        severity="low",
        category="security",
        description="minor",
        evidence_ref="src/x.py:1",
        fix="fix",
    )
    v = _make_verdict(issues=[issue])
    assert requires_escalation(v) is False


def test_escalation_clean_verdict_no_trigger() -> None:
    v = _make_verdict()
    assert requires_escalation(v) is False


# --- CRITIC_JSON_SCHEMA Export ---


def test_schema_export_is_dict() -> None:
    assert isinstance(CRITIC_JSON_SCHEMA, dict)


def test_schema_export_has_top_level_required_fields() -> None:
    required = CRITIC_JSON_SCHEMA.get("required", [])
    for field in (
        "verdict",
        "axes",
        "confidence",
        "suggested_next_action",
    ):
        assert field in required, f"Schema-Export fehlt required-Feld: {field}"


def test_schema_export_serializable_to_json() -> None:
    s = json.dumps(CRITIC_JSON_SCHEMA)
    assert len(s) > 100
    parsed = json.loads(s)
    assert parsed == CRITIC_JSON_SCHEMA


# --- Custom-Exceptions sind importierbar ---


def test_custom_exceptions_subclass_correct_base() -> None:
    assert issubclass(CriticVerdictInconsistent, ValueError)
    assert issubclass(CriticTimeout, TimeoutError)
    assert issubclass(CriticSchemaInvalid, ValueError)
