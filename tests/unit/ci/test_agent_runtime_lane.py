"""The Hermes / OpenClaw CI lane: what turns it on, what it must prove, its pins."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from scripts.ci import agent_runtime_suite, classify_changes, required_results, select_tests

ROOT = Path(__file__).resolve().parents[3]
LANE_JOBS = {"agent-runtimes-unit", "agent-runtimes-headless", "agent-runtimes-e2e"}


@pytest.mark.parametrize(
    "path",
    [
        "jarvis/agent_runtimes/gateway.py",
        "jarvis\\agent_runtimes\\hermes.py",
        "jarvis/agent_chat/runner_acp.py",
        "jarvis/society/chat_binding.py",
        "jarvis/ui/web/society_routes.py",
        "tests/fakes/fake_acp_agent.py",
        "scripts/spikes/agent_runtimes_gateway_e2e.py",
        "requirements.txt",
    ],
)
def test_runtime_paths_turn_the_lane_on(path):
    assert classify_changes.classify([path])["agent_runtimes"] is True


def test_an_unrelated_change_leaves_the_lane_off():
    assert classify_changes.classify(["jarvis/appshot/region.py"])["agent_runtimes"] is False


def test_a_pipeline_change_runs_the_lane_too():
    assert classify_changes.classify(["scripts/ci/run_gates.py"])["agent_runtimes"] is True


def _needs(lane: bool, **results: str) -> dict:
    outputs = {flag: "false" for flag in required_results.LANE_JOBS}
    outputs.update(full="false", macos="false", macos_desktop="false", release="false")
    outputs["agent_runtimes"] = "true" if lane else "false"
    needs = {"detect": {"result": "success", "outputs": outputs}, "gates": {"result": "success"}}
    needs.update({job: {"result": result} for job, result in results.items()})
    return needs


def test_the_gate_requires_every_runtime_job_when_the_lane_is_on():
    verdict = required_results.evaluate(
        _needs(True, **dict.fromkeys(LANE_JOBS, "skipped")), pipeline=True
    )
    assert verdict["ok"] is False
    assert LANE_JOBS <= set(verdict["failed"])
    passing = required_results.evaluate(
        _needs(True, **dict.fromkeys(LANE_JOBS, "success")), pipeline=True
    )
    assert passing["ok"] is True


def test_the_gate_accepts_skipped_runtime_jobs_when_the_lane_is_off():
    verdict = required_results.evaluate(
        _needs(False, **dict.fromkeys(LANE_JOBS, "skipped")), pipeline=True
    )
    assert verdict["ok"] is True


def test_a_runtime_change_selects_the_chat_and_society_chain():
    tests = [
        "tests/unit/agent_runtimes/test_acp_runner.py",
        "tests/unit/agent_chat/test_send_queue.py",
        "tests/unit/society/test_chat_binding.py",
        *[f"tests/unit/other/test_{i}.py" for i in range(40)],
    ]
    mode, chosen = select_tests.select(
        ["jarvis/agent_runtimes/manager.py"], tests, dict.fromkeys(tests, "")
    )
    assert mode == "selected"
    assert set(tests[:3]) <= set(chosen)


def test_the_suite_names_only_tests_that_exist():
    for entry in (*agent_runtime_suite.SUITE, *agent_runtime_suite.HEADLESS):
        assert (ROOT / entry).exists(), entry
    for path in agent_runtime_suite.FILES:
        assert (ROOT / path).exists(), path


def test_ci_runs_the_lane_on_every_os_and_the_gate_waits_for_it():
    jobs = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))["jobs"]
    assert "agent_runtimes" in jobs["detect"]["outputs"]
    assert LANE_JOBS <= set(jobs["gate"]["needs"])
    oses = {"ubuntu-latest", "windows-latest", "macos-latest"}
    assert set(jobs["agent-runtimes-unit"]["strategy"]["matrix"]["os"]) == oses
    e2e = jobs["agent-runtimes-e2e"]["strategy"]["matrix"]
    assert set(e2e["os"]) == oses
    assert set(e2e["runtime"]) == {"hermes", "openclaw"}
    assert jobs["agent-runtimes-headless"]["container"] == "python:3.11-slim"
    for job in LANE_JOBS:
        assert jobs[job]["if"] == "needs.detect.outputs.agent_runtimes == 'true'"


# ------------------------------------------------------------------ pins


def test_no_manifest_means_latest(tmp_path):
    found = agent_runtime_suite.pin("openclaw", tmp_path / "absent.json")
    assert found == {"version": "latest", "npm_spec": "openclaw@latest", "hermes_commit": ""}


def test_a_pin_selects_the_npm_version_and_the_hermes_commit(tmp_path):
    manifest = tmp_path / "runtime-versions.json"
    commit = "a" * 40
    manifest.write_text(
        json.dumps({"hermes": {"tested": "0.21.5", "commit": commit}, "openclaw": "2026.9.8"}),
        encoding="utf-8",
    )
    assert agent_runtime_suite.pin("hermes", manifest)["hermes_commit"] == commit
    assert agent_runtime_suite.pin("openclaw", manifest)["npm_spec"] == "openclaw@2026.9.8"


def test_a_pin_that_could_inject_a_command_is_ignored(tmp_path):
    manifest = tmp_path / "runtime-versions.json"
    manifest.write_text(
        json.dumps({"hermes": {"tested": "1; rm -rf /", "commit": "main && curl x"}}),
        encoding="utf-8",
    )
    found = agent_runtime_suite.pin("hermes", manifest)
    assert found["version"] == "latest" and found["hermes_commit"] == ""


def _report(folder: Path, name: str, runtime: str, version: str, commit: str = "") -> None:
    (folder / name).mkdir(parents=True)
    (folder / name / "canary-report.json").write_text(
        json.dumps({"runtime": runtime, "version": version, "commit": commit}), encoding="utf-8"
    )


def test_the_canary_raises_a_pin_only_when_every_os_agrees(tmp_path):
    manifest = tmp_path / "runtime-versions.json"
    manifest.write_text(json.dumps({"openclaw": {"minimum": "2026.9.8"}}), encoding="utf-8")
    reports = tmp_path / "reports"
    for os_name in ("ubuntu", "windows", "macos"):
        _report(reports, f"canary-report-openclaw-{os_name}", "openclaw", "2026.10.1")
    _report(reports, "canary-report-hermes-ubuntu", "hermes", "0.22.0", "b" * 40)
    _report(reports, "canary-report-hermes-windows", "hermes", "0.22.0", "b" * 40)
    assert agent_runtime_suite.bump_from(reports, manifest) is True
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert data["openclaw"] == {"minimum": "2026.9.8", "tested": "2026.10.1"}
    assert "hermes" not in data  # macOS did not report: the pin is kept


def test_disagreeing_oses_keep_the_pin(tmp_path):
    manifest = tmp_path / "runtime-versions.json"
    reports = tmp_path / "reports"
    _report(reports, "a", "openclaw", "2026.10.1")
    _report(reports, "b", "openclaw", "2026.10.1")
    _report(reports, "c", "openclaw", "2026.10.2")
    assert agent_runtime_suite.bump_from(reports, manifest) is False
    assert not manifest.exists()
