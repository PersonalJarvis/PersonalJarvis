"""Regression coverage for the direct Codex critic's flat output contract."""

from __future__ import annotations

import pytest

from jarvis.missions.critic.runner import (
    _CODEX_CRITIC_OUTPUT_SCHEMA,
    _verdict_from_codex_flat,
)
from jarvis.missions.critic.verdict import REQUIRED_AXES, is_approval_valid


def _flat_verdict(
    *,
    verdict: str = "approve",
    blocking_issue: bool = False,
    summary: str = "The requested deliverable is complete.",
    summary_de: str = "The requested deliverable is complete.",
) -> dict[str, object]:
    payload: dict[str, object] = {
        "verdict": verdict,
        "confidence": 0.91,
        "summary": summary,
        "summary_de": summary_de,
        "correction_instruction": "" if verdict == "approve" else "Fix the blocker.",
        "blocking_issue": blocking_issue,
    }
    for axis in REQUIRED_AXES:
        payload[f"{axis}_status"] = "pass"
        payload[f"{axis}_evidence"] = f"artifact.html:1 — {axis} verified"
    return payload


def test_schema_requires_grounded_axis_fields_and_blocking_decision() -> None:
    properties = set(_CODEX_CRITIC_OUTPUT_SCHEMA["properties"])
    required = set(_CODEX_CRITIC_OUTPUT_SCHEMA["required"])
    assert properties == required
    assert "blocking_issue" in required
    for axis in REQUIRED_AXES:
        assert f"{axis}_status" in required
        assert f"{axis}_evidence" in required


def test_overlong_flat_approval_is_truncated_not_discarded() -> None:
    verdict = _verdict_from_codex_flat(
        _flat_verdict(summary="A" * 400, summary_de="B" * 400)
    )

    assert verdict.verdict == "approve"
    assert is_approval_valid(verdict)
    assert len(verdict.summary) == 280
    assert len(verdict.summary_de) == 280
    assert verdict.summary.endswith("…")
    assert verdict.summary_de.endswith("…")


def test_legacy_five_field_approval_also_uses_tolerant_validation() -> None:
    verdict = _verdict_from_codex_flat(
        {
            "verdict": "approve",
            "confidence": 0.9,
            "summary": "A" * 400,
            "summary_de": "B" * 400,
            "correction_instruction": "",
        }
    )

    assert verdict.verdict == "approve"
    assert is_approval_valid(verdict)
    assert len(verdict.summary) == 280
    assert len(verdict.summary_de) == 280


def test_blocking_revision_preserves_truthful_axis_evidence() -> None:
    payload = _flat_verdict(verdict="revise", blocking_issue=True)
    payload["correctness_status"] = "fail"
    payload["correctness_evidence"] = "artifact.html:42 — unsafe medical claim"

    verdict = _verdict_from_codex_flat(payload)

    assert verdict.verdict == "revise"
    assert verdict.axes["correctness"].status == "fail"
    assert verdict.axes["correctness"].evidence == [
        "artifact.html:42 — unsafe medical claim"
    ]
    assert verdict.axes["security"].status == "pass"
    assert verdict.axes["security"].evidence


def test_nonblocking_all_pass_revision_is_normalized_to_approval() -> None:
    payload = _flat_verdict(verdict="revise", blocking_issue=False)
    payload["correction_instruction"] = "Optional: polish the print stylesheet."

    verdict = _verdict_from_codex_flat(payload)

    assert verdict.verdict == "approve"
    assert verdict.suggested_next_action == "accept"
    assert is_approval_valid(verdict)


def test_nonblocking_all_pass_rejection_is_normalized_to_approval() -> None:
    payload = _flat_verdict(verdict="reject", blocking_issue=False)
    payload["correction_instruction"] = ""

    verdict = _verdict_from_codex_flat(payload)

    assert verdict.verdict == "approve"
    assert verdict.suggested_next_action == "accept"
    assert is_approval_valid(verdict)


def test_blocker_without_a_failed_axis_is_rejected_as_contradictory() -> None:
    payload = _flat_verdict(verdict="revise", blocking_issue=True)

    with pytest.raises(ValueError, match="contradicts its blocking flag"):
        _verdict_from_codex_flat(payload)


def test_claimed_approval_with_a_blocker_fails_closed() -> None:
    payload = _flat_verdict(verdict="approve", blocking_issue=True)
    payload["completeness_status"] = "fail"
    payload["completeness_evidence"] = "artifact.html:8 — requested section absent"

    verdict = _verdict_from_codex_flat(payload)

    assert verdict.verdict == "revise"
    assert verdict.suggested_next_action == "retry"


def test_partial_new_axis_shape_is_rejected_instead_of_using_legacy_fallback() -> None:
    payload = {
        "verdict": "approve",
        "confidence": 0.9,
        "summary": "ok",
        "summary_de": "ok",
        "correction_instruction": "",
        "blocking_issue": False,
        "correctness_status": "pass",
    }

    with pytest.raises(ValueError, match="Incomplete Codex flat verdict"):
        _verdict_from_codex_flat(payload)


def test_empty_revision_evidence_is_rejected() -> None:
    payload = _flat_verdict(verdict="revise", blocking_issue=True)
    payload["correctness_status"] = "fail"
    payload["correctness_evidence"] = "   "

    with pytest.raises(ValueError, match="empty evidence.*correctness"):
        _verdict_from_codex_flat(payload)


def test_non_summary_schema_error_is_not_tolerated() -> None:
    payload = _flat_verdict()
    payload["confidence"] = 1.5

    with pytest.raises(ValueError):
        _verdict_from_codex_flat(payload)
