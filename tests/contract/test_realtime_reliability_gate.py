"""Contract tests for the realtime release and slow-soak gates."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

_COMMIT_SHA = "abcdef0123456789abcdef0123456789abcdef01"
_PLATFORM = "linux"
_ARCHITECTURE = "x86_64"
_TTFA_FIELD = "first_final_to_first_audio_ms"


def _load_gate():
    path = Path(__file__).resolve().parents[2] / "scripts" / "realtime_reliability_gate.py"
    spec = importlib.util.spec_from_file_location("_realtime_reliability_gate", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _report(gate: Any, **overrides: Any) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema_version": gate.SCHEMA_VERSION,
        "measurement_mode": "instrumented_target",
        "commit_sha": _COMMIT_SHA,
        "target": {"platform": _PLATFORM, "architecture": _ARCHITECTURE},
        "capabilities": {"local_inference": True},
        gate.CANONICAL_TTFA_FIELD: [600.0] * 100,
        "barge_stop_ms": [120.0] * 20,
        "failback_ms": [900.0] * 20,
        "unaffected_turn_regression_ms": [10.0] * 50,
        "turns_completed": 500,
        "orphan_deliveries": 0,
        "duplicate_deliveries": 0,
        "scenario_results": {
            "three_turns_de": True,
            "barge_in_followup": True,
            "delegation": True,
            "detached_delegate_delivery": True,
            "provider_replacement": True,
            "classic_delivery": True,
        },
    }
    report.update(overrides)
    report["provenance"] = {
        "collector": gate.CANONICAL_COLLECTOR,
        "collector_version": gate.CANONICAL_COLLECTOR_VERSION,
        "event_source": gate.CANONICAL_EVENT_SOURCE,
        "clock": gate.CANONICAL_CLOCK,
        "measurement_id": "00000000-0000-4000-8000-000000000001",
        "started_at": "2026-08-09T10:00:00Z",
        "completed_at": "2026-08-09T10:05:00Z",
    }
    report["provenance"]["evidence_sha256"] = gate.report_evidence_sha256(report)
    return report


def _evaluate(gate: Any, **overrides: Any):
    return gate.evaluate_report(
        _report(gate, **overrides),
        expected_sha=_COMMIT_SHA,
        expected_platform=_PLATFORM,
        expected_architecture=_ARCHITECTURE,
    )


def test_instrumented_target_report_passes_every_slo() -> None:
    gate = _load_gate()
    result = _evaluate(gate)

    assert result.passed is True
    assert result.warm_connects == 100
    assert result.turns == 500


@pytest.mark.parametrize(
    ("override", "failure_fragment"),
    [
        ({"measurement_mode": "synthetic"}, "instrumented_target"),
        ({_TTFA_FIELD: [600.0] * 99}, "need at least 100"),
        ({"turns_completed": 499}, "need at least 500"),
        ({"orphan_deliveries": 1}, "orphan_deliveries"),
        ({"duplicate_deliveries": 1}, "duplicate_deliveries"),
        ({"barge_stop_ms": [251.0] * 20}, "barge stop p95"),
        ({"failback_ms": [2_001.0] * 20}, "failback max"),
        (
            {"unaffected_turn_regression_ms": [51.0] * 50},
            "unaffected-turn regression",
        ),
        (
            {
                "scenario_results": {
                    "three_turns_de": True,
                    "barge_in_followup": False,
                    "delegation": True,
                }
            },
            "barge_in_followup",
        ),
    ],
)
def test_release_gate_fails_closed(override: dict[str, Any], failure_fragment: str) -> None:
    gate = _load_gate()
    result = _evaluate(gate, **override)

    assert result.passed is False
    assert any(failure_fragment in failure for failure in result.failures)


def test_release_gate_checks_interpolated_ttfa_percentiles() -> None:
    gate = _load_gate()
    values = [700.0] * 94 + [1_500.0] * 6
    result = _evaluate(gate, **{gate.CANONICAL_TTFA_FIELD: values})

    assert result.passed is False
    assert result.ttfa_p50_ms <= 800.0
    assert result.ttfa_p95_ms > 1_200.0


def test_small_provider_neutral_soak_exercises_real_session_state_machine() -> None:
    gate = _load_gate()
    metrics = asyncio.run(
        gate.run_contract_soak(
            warm_connects=2,
            turns_per_connect=2,
            barge_samples=1,
            failback_samples=1,
        )
    )

    assert metrics["warm_connects"] == 2
    assert metrics["turns"] == 4
    assert metrics["orphan_deliveries"] == 0
    assert metrics["duplicate_deliveries"] == 0
