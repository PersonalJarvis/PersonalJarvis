"""Deterministic MacAgentBench scenario contracts.

This module does not drive the desktop. It defines receipts/evaluators that a
later explicit live-Mac runner can populate after native actions. Keeping the
evaluation pure makes CI useful without requesting TCC permissions or posting
synthetic input.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class MacAgentBenchScenario:
    id: str
    description: str
    live_required: bool
    readiness_checks: tuple[str, ...]
    success_criteria: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


PHYSICAL_USER_TAKEOVER = MacAgentBenchScenario(
    id="physical-user-takeover",
    description=(
        "A person uses physical mouse/keyboard input while macOS Computer-Use "
        "owns the desktop; Jarvis must yield and re-observe before resuming."
    ),
    live_required=True,
    readiness_checks=(
        "handoff:hardware-input",
        "actuation:backend",
        "semantic:ax-tree",
    ),
    success_criteria=(
        "physical HID activity is detected",
        "no additional Jarvis synthetic event is posted after takeover is observed",
        "automation stays paused until hardware input is idle",
        "the desktop is re-observed before the next automated action",
        "cancellation during the handoff produces no later action",
    ),
)


SEMANTIC_TARGET_HIT = MacAgentBenchScenario(
    id="semantic-target-hit",
    description=(
        "A labelled macOS control is re-identified from fresh Accessibility state "
        "and actuated natively without falling back to stale pixels."
    ),
    live_required=True,
    readiness_checks=(
        "semantic:ax-tree",
    ),
    success_criteria=(
        "the observed target is re-identified in the live Accessibility tree",
        "foreground window identity stays stable through the native mutation boundary",
        "the native Accessibility action is performed",
        "no pointer event is posted after semantic success",
        "the expected UI effect is verified after the action",
    ),
)


def macagentbench_scenarios() -> tuple[MacAgentBenchScenario, ...]:
    """Return the currently specified MacAgentBench live scenarios."""
    return (
        PHYSICAL_USER_TAKEOVER,
        SEMANTIC_TARGET_HIT,
    )


@dataclass(frozen=True)
class PhysicalTakeoverReceipt:
    """Evidence captured by a live/fake physical-takeover run."""

    takeover_detected: bool
    synthetic_events_after_takeover: int
    ownership_became_idle: bool
    resumed_after_idle: bool
    reobserved_before_next_action: bool
    cancellation_requested: bool = False
    action_after_cancel: bool = False


@dataclass(frozen=True)
class SemanticTargetHitReceipt:
    """Evidence captured by a live/fake semantic-target run."""

    target_reidentified: bool
    foreground_identity_stable: bool
    native_action_performed: bool
    pointer_events_posted: int
    expected_effect_verified: bool


@dataclass(frozen=True)
class MacAgentBenchEvaluation:
    scenario_id: str
    passed: bool
    failures: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_physical_takeover(
    receipt: PhysicalTakeoverReceipt,
) -> MacAgentBenchEvaluation:
    """Evaluate a takeover receipt without touching the host desktop."""
    failures: list[str] = []

    if not receipt.takeover_detected:
        failures.append("physical HID takeover was not detected")
    if receipt.synthetic_events_after_takeover != 0:
        failures.append(
            "Jarvis posted synthetic input after takeover detection "
            f"({receipt.synthetic_events_after_takeover} event(s))"
        )
    if not receipt.ownership_became_idle:
        failures.append("hardware input never became idle during the scenario")
    if receipt.resumed_after_idle and not receipt.ownership_became_idle:
        failures.append("automation resumed before hardware ownership was idle")
    if receipt.ownership_became_idle and not receipt.resumed_after_idle:
        failures.append("automation did not resume after hardware input became idle")
    if receipt.resumed_after_idle and not receipt.reobserved_before_next_action:
        failures.append("automation resumed without re-observing the desktop")
    if receipt.cancellation_requested and receipt.action_after_cancel:
        failures.append("an automated action occurred after cancellation")

    return MacAgentBenchEvaluation(
        scenario_id=PHYSICAL_USER_TAKEOVER.id,
        passed=not failures,
        failures=tuple(failures),
    )


def evaluate_semantic_target_hit(
    receipt: SemanticTargetHitReceipt,
) -> MacAgentBenchEvaluation:
    """Evaluate semantic actuation evidence without touching the host desktop."""
    failures: list[str] = []

    if not receipt.target_reidentified:
        failures.append("the semantic target was not re-identified from fresh Accessibility state")
    if not receipt.foreground_identity_stable:
        failures.append("foreground window identity changed before the native semantic action")
    if not receipt.native_action_performed:
        failures.append("the native Accessibility action was not performed")
    if receipt.pointer_events_posted != 0:
        failures.append(
            "pointer fallback occurred after semantic target selection "
            f"({receipt.pointer_events_posted} event(s))"
        )
    if not receipt.expected_effect_verified:
        failures.append("the expected UI effect was not verified after the semantic action")

    return MacAgentBenchEvaluation(
        scenario_id=SEMANTIC_TARGET_HIT.id,
        passed=not failures,
        failures=tuple(failures),
    )


__all__ = [
    "MacAgentBenchEvaluation",
    "MacAgentBenchScenario",
    "PHYSICAL_USER_TAKEOVER",
    "SEMANTIC_TARGET_HIT",
    "PhysicalTakeoverReceipt",
    "SemanticTargetHitReceipt",
    "evaluate_physical_takeover",
    "evaluate_semantic_target_hit",
    "macagentbench_scenarios",
]
