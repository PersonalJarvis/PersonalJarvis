"""MacAgentBench physical-user-takeover receipt tests."""
from __future__ import annotations

from jarvis.cu.macos_bench import (
    PHYSICAL_USER_TAKEOVER,
    PhysicalTakeoverReceipt,
    evaluate_physical_takeover,
    macagentbench_scenarios,
)


def _passing_receipt(**overrides):
    values = {
        "takeover_detected": True,
        "synthetic_events_after_takeover": 0,
        "ownership_became_idle": True,
        "resumed_after_idle": True,
        "reobserved_before_next_action": True,
        "cancellation_requested": False,
        "action_after_cancel": False,
    }
    values.update(overrides)
    return PhysicalTakeoverReceipt(**values)


def test_takeover_scenario_is_live_gated_and_bound_to_readiness() -> None:
    assert PHYSICAL_USER_TAKEOVER in macagentbench_scenarios()
    assert PHYSICAL_USER_TAKEOVER.live_required is True
    assert "handoff:hardware-input" in PHYSICAL_USER_TAKEOVER.readiness_checks
    assert "actuation:backend" in PHYSICAL_USER_TAKEOVER.readiness_checks


def test_safe_takeover_receipt_passes() -> None:
    result = evaluate_physical_takeover(_passing_receipt())

    assert result.passed is True
    assert result.failures == ()


def test_synthetic_event_after_takeover_fails() -> None:
    result = evaluate_physical_takeover(
        _passing_receipt(synthetic_events_after_takeover=1)
    )

    assert result.passed is False
    assert any("after takeover" in failure for failure in result.failures)


def test_resume_requires_idle_and_reobservation() -> None:
    result = evaluate_physical_takeover(
        _passing_receipt(
            ownership_became_idle=False,
            resumed_after_idle=True,
            reobserved_before_next_action=False,
        )
    )

    assert result.passed is False
    assert any("before hardware ownership was idle" in failure for failure in result.failures)
    assert any("without re-observing" in failure for failure in result.failures)


def test_cancelled_takeover_forbids_later_action() -> None:
    result = evaluate_physical_takeover(
        _passing_receipt(cancellation_requested=True, action_after_cancel=True)
    )

    assert result.passed is False
    assert any("after cancellation" in failure for failure in result.failures)
