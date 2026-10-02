"""Deterministic MacAgentBench receipt evaluator tests."""
from __future__ import annotations

from dataclasses import replace

import pytest

from jarvis.cu.macos_bench import (
    BROWSER_DESKTOP_HANDOFF,
    CROSS_WINDOW_HANDOFF,
    FOCUS_TYPE_LANDING,
    PHYSICAL_USER_TAKEOVER,
    SEMANTIC_TARGET_HIT,
    STALE_TARGET_REFUSAL,
    BrowserDesktopHandoffReceipt,
    CrossWindowHandoffReceipt,
    FocusTypeLandingReceipt,
    PhysicalTakeoverReceipt,
    SemanticTargetHitReceipt,
    StaleTargetRefusalReceipt,
    evaluate_browser_desktop_handoff,
    evaluate_cross_window_handoff,
    evaluate_focus_type_landing,
    evaluate_physical_takeover,
    evaluate_semantic_target_hit,
    evaluate_stale_target_refusal,
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


def _passing_semantic_receipt(**overrides):
    values = {
        "target_reidentified": True,
        "foreground_identity_stable": True,
        "native_action_performed": True,
        "pointer_events_posted": 0,
        "expected_effect_verified": True,
    }
    values.update(overrides)
    return SemanticTargetHitReceipt(**values)


def _passing_stale_refusal_receipt(**overrides):
    values = {
        "target_was_freshly_observed": True,
        "identity_drift_detected": True,
        "native_action_performed": False,
        "synthetic_events_posted": 0,
        "refusal_reported": True,
        "reobserve_requested": True,
    }
    values.update(overrides)
    return StaleTargetRefusalReceipt(**values)


def _passing_focus_type_receipt(**overrides):
    values = {
        "target_reidentified": True,
        "focus_performed": True,
        "foreground_identity_stable": True,
        "requested_characters": 12,
        "landed_characters": 12,
        "pointer_events_posted": 0,
        "secure_value_read": False,
        "expected_effect_verified": True,
    }
    values.update(overrides)
    return FocusTypeLandingReceipt(**values)


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


def test_semantic_target_scenario_is_live_gated_and_bound_to_ax_tree() -> None:
    assert SEMANTIC_TARGET_HIT in macagentbench_scenarios()
    assert SEMANTIC_TARGET_HIT.live_required is True
    assert "semantic:ax-tree" in SEMANTIC_TARGET_HIT.readiness_checks


def test_semantic_target_receipt_passes_without_pointer_fallback() -> None:
    result = evaluate_semantic_target_hit(_passing_semantic_receipt())

    assert result.passed is True
    assert result.failures == ()


def test_semantic_target_requires_fresh_identity_and_stable_foreground() -> None:
    result = evaluate_semantic_target_hit(
        _passing_semantic_receipt(
            target_reidentified=False,
            foreground_identity_stable=False,
        )
    )

    assert result.passed is False
    assert any("re-identified" in failure for failure in result.failures)
    assert any("foreground window identity changed" in failure for failure in result.failures)


def test_semantic_success_forbids_pointer_fallback_and_requires_effect_verification() -> None:
    result = evaluate_semantic_target_hit(
        _passing_semantic_receipt(
            pointer_events_posted=1,
            expected_effect_verified=False,
        )
    )

    assert result.passed is False
    assert any("pointer fallback" in failure for failure in result.failures)
    assert any("expected UI effect" in failure for failure in result.failures)


def test_stale_target_scenario_is_live_gated_and_bound_to_actuation() -> None:
    assert STALE_TARGET_REFUSAL in macagentbench_scenarios()
    assert STALE_TARGET_REFUSAL.live_required is True
    assert "semantic:ax-tree" in STALE_TARGET_REFUSAL.readiness_checks
    assert "actuation:backend" in STALE_TARGET_REFUSAL.readiness_checks


def test_stale_target_refusal_receipt_passes_when_no_mutation_occurs() -> None:
    result = evaluate_stale_target_refusal(_passing_stale_refusal_receipt())

    assert result.passed is True
    assert result.failures == ()


def test_stale_target_refusal_requires_drift_detection_and_report() -> None:
    result = evaluate_stale_target_refusal(
        _passing_stale_refusal_receipt(
            identity_drift_detected=False,
            refusal_reported=False,
        )
    )

    assert result.passed is False
    assert any("identity drift" in failure for failure in result.failures)
    assert any("not surfaced" in failure for failure in result.failures)


def test_stale_target_refusal_forbids_native_and_synthetic_mutation() -> None:
    result = evaluate_stale_target_refusal(
        _passing_stale_refusal_receipt(
            native_action_performed=True,
            synthetic_events_posted=1,
        )
    )

    assert result.passed is False
    assert any("native semantic action" in failure for failure in result.failures)
    assert any("synthetic input" in failure for failure in result.failures)


def test_stale_target_refusal_requires_fresh_reobservation() -> None:
    result = evaluate_stale_target_refusal(
        _passing_stale_refusal_receipt(
            target_was_freshly_observed=False,
            reobserve_requested=False,
        )
    )

    assert result.passed is False
    assert any("fresh observation" in failure for failure in result.failures)



def test_focus_type_scenario_is_live_gated_and_bound_to_semantics() -> None:
    assert FOCUS_TYPE_LANDING in macagentbench_scenarios()
    assert FOCUS_TYPE_LANDING.live_required is True
    assert "semantic:ax-tree" in FOCUS_TYPE_LANDING.readiness_checks
    assert "actuation:backend" in FOCUS_TYPE_LANDING.readiness_checks


def test_focus_type_receipt_passes_for_guarded_non_secret_probe() -> None:
    result = evaluate_focus_type_landing(_passing_focus_type_receipt())

    assert result.passed is True
    assert result.failures == ()


def test_focus_type_requires_semantic_focus_and_stable_foreground() -> None:
    result = evaluate_focus_type_landing(
        _passing_focus_type_receipt(
            focus_performed=False,
            foreground_identity_stable=False,
        )
    )

    assert result.passed is False
    assert any("AXFocused" in failure for failure in result.failures)
    assert any("foreground window identity changed" in failure for failure in result.failures)


def test_focus_type_requires_full_probe_landing_without_pointer_input() -> None:
    result = evaluate_focus_type_landing(
        _passing_focus_type_receipt(
            landed_characters=9,
            pointer_events_posted=1,
        )
    )

    assert result.passed is False
    assert any("did not fully land" in failure for failure in result.failures)
    assert any("pointer input" in failure for failure in result.failures)


def test_focus_type_evidence_never_reads_secure_field_value() -> None:
    result = evaluate_focus_type_landing(
        _passing_focus_type_receipt(secure_value_read=True)
    )

    assert result.passed is False
    assert any("secure text-field content" in failure for failure in result.failures)


@pytest.fixture
def cross_window_receipt() -> CrossWindowHandoffReceipt:
    return CrossWindowHandoffReceipt(
        source_window_id=101,
        intended_window_id=202,
        observed_window_id=202,
        transition_authorized=True,
        destination_reobserved=True,
        destination_target_reidentified=True,
        foreground_identity_stable=True,
        stale_source_actions=0,
        expected_effect_verified=True,
    )


@pytest.fixture
def browser_desktop_receipt() -> BrowserDesktopHandoffReceipt:
    return BrowserDesktopHandoffReceipt(
        handoff_target="core:computer-use",
        browser_actions_after_handoff=0,
        desktop_routed_through_executor=True,
        destination_reobserved=True,
        foreground_identity_stable=True,
        expected_effect_verified=True,
    )


@pytest.mark.parametrize("scenario", [CROSS_WINDOW_HANDOFF, BROWSER_DESKTOP_HANDOFF])
def test_handoff_scenarios_remain_live_gated(scenario) -> None:
    scenarios = macagentbench_scenarios()
    assert scenario in scenarios
    assert len({entry.id for entry in scenarios}) == len(scenarios)
    assert scenario.to_dict()["live_required"] is True
    assert set(scenario.readiness_checks) == {
        "semantic:ax-tree", "actuation:backend", "handoff:hardware-input",
    }


def test_cross_window_receipt_passes_for_fresh_authorized_destination(cross_window_receipt):
    result = evaluate_cross_window_handoff(cross_window_receipt)
    assert result.to_dict() == {
        "scenario_id": "cross-window-handoff", "passed": True, "failures": (),
    }


@pytest.mark.parametrize(
    ("changes", "failure"),
    [
        ({"source_window_id": 0}, "known positive IDs"),
        ({"intended_window_id": -1}, "known positive IDs"),
        ({"observed_window_id": None}, "known positive IDs"),
        ({"source_window_id": True}, "known positive IDs"),
        ({"source_window_id": "101"}, "known positive IDs"),
        ({"source_window_id": 202}, "distinct destination"),
        ({"observed_window_id": 101}, "not the intended window"),
        ({"observed_window_id": 303}, "not the intended window"),
        ({"transition_authorized": False}, "authorized Computer-Use"),
        ({"destination_reobserved": False}, "not re-observed"),
        ({"destination_target_reidentified": False}, "not re-identified"),
        ({"foreground_identity_stable": False}, "foreground identity changed"),
        ({"stale_source_actions": 1}, "stale source-window"),
        ({"stale_source_actions": -1}, "stale source-window"),
        ({"expected_effect_verified": False}, "effect was not verified"),
        ({"cancellation_requested": True, "action_after_cancel": True}, "after cancellation"),
    ],
)
def test_cross_window_handoff_rejects_unsafe_or_missing_evidence(
    cross_window_receipt, changes, failure,
):
    result = evaluate_cross_window_handoff(replace(cross_window_receipt, **changes))
    assert result.passed is False
    assert any(failure in message for message in result.failures)


def test_browser_desktop_receipt_passes_without_requiring_another_window(browser_desktop_receipt):
    result = evaluate_browser_desktop_handoff(browser_desktop_receipt)
    assert result.to_dict() == {
        "scenario_id": "browser-to-desktop-handoff", "passed": True, "failures": (),
    }


@pytest.mark.parametrize(
    ("changes", "failure"),
    [
        ({"handoff_target": ""}, "explicitly hand off"),
        ({"handoff_target": "society_browser"}, "explicitly hand off"),
        ({"browser_actions_after_handoff": 1}, "browser specialist acted"),
        ({"browser_actions_after_handoff": -1}, "browser specialist acted"),
        ({"desktop_routed_through_executor": False}, "ToolExecutor safety boundary"),
        ({"destination_reobserved": False}, "not re-observed"),
        ({"foreground_identity_stable": False}, "foreground identity changed"),
        ({"expected_effect_verified": False}, "effect was not verified"),
        ({"cancellation_requested": True, "action_after_cancel": True}, "after cancellation"),
    ],
)
def test_browser_desktop_handoff_rejects_unsafe_or_missing_evidence(
    browser_desktop_receipt, changes, failure,
):
    result = evaluate_browser_desktop_handoff(replace(browser_desktop_receipt, **changes))
    assert result.passed is False
    assert any(failure in message for message in result.failures)


def test_handoff_cancellation_stops_actions_after_completed_transition(
    cross_window_receipt, browser_desktop_receipt,
):
    # The successful transition precedes cancellation; no action follows it.
    for receipt, evaluate in (
        (cross_window_receipt, evaluate_cross_window_handoff),
        (browser_desktop_receipt, evaluate_browser_desktop_handoff),
    ):
        assert evaluate(replace(receipt, cancellation_requested=True)).passed is True
