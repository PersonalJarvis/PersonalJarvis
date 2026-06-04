"""AgentDoG diagnostic taxonomy: enums + Diagnostic record + diagnose()."""

from __future__ import annotations

import pytest

from skillbook.guardrails.diagnostics import (
    AgentDoG,
    Consequence,
    Diagnostic,
    FailureMode,
    Source,
)


def test_enum_values_are_stable_strings() -> None:
    # Five-layer enum pattern: values are stable across versions.
    assert Source.ACTOR_INVOCATION.value == "actor_invocation"
    assert FailureMode.TIMEOUT.value == "timeout"
    assert Consequence.RECOVERABLE.value == "recoverable"


def test_diagnose_timeout_produces_actor_invocation_diagnostic() -> None:
    dog = AgentDoG()
    diag = dog.diagnose(
        source=Source.ACTOR_INVOCATION,
        actor="magic_home_controller",
        exception=TimeoutError("call exceeded 2.0s"),
    )
    assert isinstance(diag, Diagnostic)
    assert diag.source == Source.ACTOR_INVOCATION
    assert diag.failure_mode == FailureMode.TIMEOUT
    assert diag.consequence == Consequence.RECOVERABLE
    assert "magic_home_controller" in diag.evidence


def test_diagnose_proposes_retry_rule_for_timeout() -> None:
    dog = AgentDoG()
    diag = dog.diagnose(
        source=Source.ACTOR_INVOCATION,
        actor="x_controller",
        exception=TimeoutError("..."),
    )
    assert diag.suggested_rule is not None
    assert diag.suggested_rule["trigger"]["actor"] == "x_controller"
    assert diag.suggested_rule["strategy"]["kind"] == "retry_with_delay"


def test_diagnose_unknown_exception_returns_inconsistent_state() -> None:
    dog = AgentDoG()
    diag = dog.diagnose(
        source=Source.ACTOR_INVOCATION,
        actor="z",
        exception=ValueError("unexpected payload"),
    )
    assert diag.failure_mode == FailureMode.INCONSISTENT_STATE
    # No retry rule for unknown errors — would just repeat the bug.
    assert diag.suggested_rule is None


def test_diagnostic_serializes_to_json_with_string_enum_values() -> None:
    diag = Diagnostic(
        source=Source.PLANNING_STEP,
        failure_mode=FailureMode.POLICY_VIOLATION,
        consequence=Consequence.SECURITY_BREACH,
        evidence="rule X forbids action Y",
    )
    blob = diag.model_dump_json()
    assert '"source":"planning_step"' in blob
    assert '"failure_mode":"policy_violation"' in blob
    assert '"consequence":"security_breach"' in blob


def test_diagnose_rejects_missing_actor_for_actor_invocation_source() -> None:
    dog = AgentDoG()
    with pytest.raises(ValueError):
        dog.diagnose(
            source=Source.ACTOR_INVOCATION,
            actor=None,
            exception=TimeoutError("..."),
        )
