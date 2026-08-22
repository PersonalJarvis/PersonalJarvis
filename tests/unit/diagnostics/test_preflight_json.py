"""`jarvis --check --json` / `jarvis --doctor --json` emit a stable machine contract.

The point of the JSON path is that an installer can branch on it. That only
holds if the status vocabulary stays closed and the exit codes keep the meaning
they had before the flag existed — both are asserted here.
"""
from __future__ import annotations

import json

import pytest

import jarvis.__main__ as jm
from jarvis.diagnostics.json_report import FIELDS, STATUSES, dumps, record
from jarvis.hardware.detection import (
    GPUInfo,
    HardwareReport,
    WhisperRecommendation,
    check_records,
)


def _report(**overrides) -> HardwareReport:
    base = dict(
        os_name="Linux",
        os_version="6.8.0 #1 SMP",
        cpu_name="Generic CPU",
        cpu_cores_physical=4,
        cpu_cores_logical=8,
        ram_total_mb=16384,
        ram_available_mb=8192,
        python_version="3.11.9",
        python_executable="/usr/bin/python3",
        ffmpeg_version="6.1",
    )
    base.update(overrides)
    return HardwareReport(**base)


def _rec() -> WhisperRecommendation:
    return WhisperRecommendation(
        provider="faster-whisper",
        model="base",
        device="cpu",
        compute_type="int8",
        expected_latency_ms=1200,
        rationale="No CUDA-capable GPU detected.",
    )


# ---------------------------------------------------------------- schema


def test_record_rejects_a_status_outside_the_closed_set():
    with pytest.raises(ValueError, match="vocabulary is closed"):
        record("brain", "degraded", "…")  # type: ignore[arg-type]


def test_every_record_carries_exactly_the_declared_fields():
    for rec in check_records(_report(), _rec()):
        assert set(rec) == set(FIELDS)
        assert rec["status"] in STATUSES


def test_dumps_emits_one_json_object_per_line():
    text = dumps(check_records(_report(), _rec()))
    lines = text.splitlines()
    assert lines
    for line in lines:
        parsed = json.loads(line)
        assert list(parsed) == list(FIELDS)  # field order is part of the contract


# ---------------------------------------------------------------- verdicts


def test_missing_ffmpeg_is_a_warning_with_an_actionable_hint():
    recs = {r["component"]: r for r in check_records(_report(ffmpeg_version=None), _rec())}
    assert recs["ffmpeg"]["status"] == "warn"
    assert recs["ffmpeg"]["hint"]


def test_ram_verdict_uses_the_same_8gb_threshold_as_the_recommendation():
    # Exactly at the threshold recommend_whisper still chooses local STT.
    at = {r["component"]: r for r in check_records(_report(ram_total_mb=8192), _rec())}
    assert at["ram"]["status"] == "ok"
    below = {r["component"]: r for r in check_records(_report(ram_total_mb=8191), _rec())}
    assert below["ram"]["status"] == "warn"
    assert below["ram"]["hint"]


def test_an_absent_gpu_is_information_never_a_failure():
    recs = {r["component"]: r for r in check_records(_report(), _rec())}
    assert recs["gpu"]["status"] == "info"


def test_a_detected_gpu_is_reported_per_device():
    report = _report(
        gpus=[
            GPUInfo(name="NVIDIA A", vram_mb=8000),
            GPUInfo(name="NVIDIA B", vram_mb=4000, compute_capability="8.6"),
        ]
    )
    recs = {r["component"]: r for r in check_records(report, _rec())}
    assert recs["gpu0"]["status"] == "ok"
    assert "8.6" in str(recs["gpu1"]["message"])


# ---------------------------------------------------------------- CLI wiring


def test_check_json_keeps_exit_code_zero(monkeypatch, capsys):
    monkeypatch.setattr(jm, "_run_control", lambda argv: pytest.fail("must not route"))
    assert jm.main(["--check", "--json"]) == 0
    out = capsys.readouterr().out.strip()
    assert out
    for line in out.splitlines():
        assert json.loads(line)["status"] in STATUSES


def test_doctor_json_still_fails_on_a_hard_failure(monkeypatch, capsys):
    from jarvis.diagnostics.doctor import DoctorFinding

    monkeypatch.setattr(
        "jarvis.diagnostics.doctor.run_doctor",
        lambda config: [DoctorFinding("router-tools", "fail", "phantom tool", "fix it")],
    )
    assert jm.main(["--doctor", "--json"]) == 1
    parsed = json.loads(capsys.readouterr().out.strip())
    assert parsed == {
        "component": "router-tools",
        "status": "fail",
        "message": "phantom tool",
        "hint": "fix it",
    }


def test_doctor_json_returns_zero_without_failures(monkeypatch, capsys):
    from jarvis.diagnostics.doctor import DoctorFinding

    monkeypatch.setattr(
        "jarvis.diagnostics.doctor.run_doctor",
        lambda config: [DoctorFinding("brain-provider", "warn", "no key", None)],
    )
    assert jm.main(["--doctor", "--json"]) == 0
    assert json.loads(capsys.readouterr().out.strip())["hint"] is None
