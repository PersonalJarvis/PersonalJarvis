"""Native one-key qualification is explicit, bounded, and credential-contained."""

from __future__ import annotations

import hashlib
import json
import runpy
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from jarvis.core.swarm_types import TeamCreate
from tests.fakes.swarm_packaging import NativeSmokeApi, NativeSmokeHarness, OfflineProviderRegistry

ROOT = Path(__file__).resolve().parents[2]
SMOKE = runpy.run_path(str(ROOT / "packaging/verify_swarm_install.py"))


@pytest.fixture
def live_setup(monkeypatch):
    import jarvis.brain.provider_registry
    from jarvis.core.config import PROVIDER_SECRET_CANDIDATES

    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    monkeypatch.setenv("SWARM_INSTALL_TEST_KEY", "offline-placeholder")
    monkeypatch.setitem(
        PROVIDER_SECRET_CANDIDATES, "fixture-api", (("fixture", "FIXTURE_API_KEY"),)
    )
    monkeypatch.setitem(PROVIDER_SECRET_CANDIDATES, "ambient-agent", (("fixture", "AMBIENT_KEY"),))
    monkeypatch.setattr(
        jarvis.brain.provider_registry, "BrainProviderRegistry", OfflineProviderRegistry
    )


@pytest.fixture
def harness(monkeypatch, tmp_path):
    harness = NativeSmokeHarness()
    namespace = SMOKE["run"].__globals__
    monkeypatch.setitem(namespace, "install", harness.install)
    monkeypatch.setitem(namespace, "running_app", harness.running_app)
    monkeypatch.setitem(namespace, "SwarmApi", lambda *args: harness.api)
    installer = tmp_path / "offline-installer"
    installer.write_bytes(b"offline-payload")
    return harness, installer, tmp_path / "report.json"


def test_live_environment_forwards_only_one_explicit_provider_key(
    live_setup, monkeypatch, tmp_path
):
    monkeypatch.setenv("OPENAI_API_KEY", "unrelated-placeholder")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "unrelated-placeholder")
    monkeypatch.setenv("JARVIS__BRAIN__WORKER__FALLBACK_PROVIDER", "inherited-provider")
    base = SMOKE["isolated_environment"](tmp_path, 47899, "offline-control")
    env = SMOKE["live_environment"](base, "fixture-api", "fixture-model")
    assert env["FIXTURE_API_KEY"] == "offline-placeholder"
    assert env["JARVIS__BRAIN__WORKER__PROVIDER"] == "fixture-api"
    assert env["JARVIS__BRAIN__WORKER__MODEL"] == "fixture-model"
    assert (
        not {
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "SWARM_INSTALL_TEST_KEY",
            "JARVIS__BRAIN__WORKER__FALLBACK_PROVIDER",
        }
        & env.keys()
    )
    assert "FIXTURE_API_KEY" not in base


@pytest.mark.parametrize("event", ["push", "pull_request", "schedule", ""])
def test_live_mode_requires_manual_dispatch(live_setup, monkeypatch, event):
    monkeypatch.setenv("GITHUB_EVENT_NAME", event)
    with pytest.raises(SMOKE["LiveVerificationError"], match="manual workflow dispatch"):
        SMOKE["live_environment"]({}, "fixture-api", "")


@pytest.mark.parametrize("provider", ["ambient-agent", "unknown-api", "$(invalid)", ""])
def test_live_mode_requires_supported_scoped_api(live_setup, provider):
    with pytest.raises(SMOKE["LiveVerificationError"]):
        SMOKE["live_environment"]({}, provider, "")


def test_missing_live_key_fails_before_install_or_api(live_setup, monkeypatch, harness):
    probe, installer, report = harness
    monkeypatch.delenv("SWARM_INSTALL_TEST_KEY")
    with pytest.raises(SMOKE["LiveVerificationError"], match="SWARM_INSTALL_TEST_KEY"):
        SMOKE["run"](installer, report, live_provider="fixture-api")
    assert probe.installer_environments == probe.api.calls == []
    result = json.loads(report.read_text())
    assert result["status"] == result["live_provider_verification"]["status"] == "failed"
    assert result["provider_requests"] == 0


def test_default_smoke_never_enables_live_mode_from_an_inherited_secret(
    live_setup, monkeypatch, harness
):
    probe, installer, report = harness
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    result = SMOKE["run"](installer, report)
    assert result["status"] == "pass"
    assert result["provider_requests"] == 0
    assert result["live_provider_verification"] == {"status": "not-run", "reason": "disabled"}
    assert len(probe.api.teams) == 1
    assert all("FIXTURE_API_KEY" not in env for env in probe.app_environments)
    assert all(not body or not body.get("tasks") for _, body in probe.api.calls)


def test_live_fresh_install_and_replacement_verify_accepted_artifact(live_setup, harness):
    probe, installer, report = harness
    result = SMOKE["run"](
        installer, report, live_provider="fixture-api", live_model="fixture-model"
    )
    live = result["live_provider_verification"]
    assert result["status"] == live["status"] == live["same_artifact_replacement"] == "pass"
    assert live["result"] == {"count": 4, "sum": 40, "mean": 10}
    assert live["credential_count"] == 1 and live["tokens_used"] == "900"
    assert probe.live_output == [True, True]
    assert "FIXTURE_API_KEY" in probe.app_environments[0]
    assert "FIXTURE_API_KEY" not in probe.app_environments[1]
    assert all("FIXTURE_API_KEY" not in env for env in probe.installer_environments)
    assert "offline-placeholder" not in report.read_text()
    assert not list(report.parent.glob("*.log"))


def test_live_task_uses_production_schema_and_bounded_non_network_tools():
    spec = TeamCreate.model_validate(SMOKE["live_task_spec"]())
    assert spec.limits.runtime_seconds == 180
    assert int(spec.limits.token_budget) == 60000
    assert spec.limits.max_attempts == 1 and spec.limits.max_tool_calls == 6
    assert not spec.policy.internet and not spec.policy.allow_dependencies
    assert set(spec.policy.tools) == {"run_javascript", "write_artifact", "read_artifact"}
    assert len(spec.tasks) == 1 and spec.tasks[0].verification == "javascript"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mean,saved,accepted",
    [
        (10, '{"count":4,"sum":40,"mean":10}', True),
        (11, '{"count":4,"sum":40,"mean":10}', False),
        (10, '{"count":4,"sum":40,"mean":11}', False),
        (10, "invalid JSON", False),
    ],
)
async def test_native_live_acceptance_script_checks_computation_and_saved_bytes(
    mean, saved, accepted
):
    from jarvis.swarm.sandbox import WasmSandbox

    spec = TeamCreate.model_validate(SMOKE["live_task_spec"]())
    result = await WasmSandbox().run(
        spec.tasks[0].verification_script,
        {
            "result": {"count": 4, "sum": 40, "mean": mean},
            "artifacts": [{"name": "statistics.json", "content": saved}],
        },
    )
    assert result.exit_code == 0
    assert result.output["accepted"] is accepted


@pytest.mark.parametrize("state", ["blocked", "failed", "canceled", "paused"])
def test_live_failure_does_not_report_success_or_provider_body(state):
    api = NativeSmokeApi()
    api.final_state = state
    with pytest.raises(SMOKE["LiveVerificationError"], match="stopped before acceptance"):
        SMOKE["run_live_task"](api, "fixture-api")


@pytest.mark.parametrize("used,reserved", [("0", "0"), ("60001", "0"), ("900", "1")])
def test_live_proof_requires_nonzero_settled_usage(used, reserved):
    api = NativeSmokeApi()
    api.tokens_used, api.tokens_reserved = used, reserved
    with pytest.raises(SMOKE["LiveVerificationError"], match="settled bounded provider usage"):
        SMOKE["run_live_task"](api, "fixture-api")


@pytest.mark.parametrize("mutation", ["unaccepted", "stale", "forged", "hash", "wrong-result"])
def test_live_proof_rejects_untrusted_or_incorrect_saved_results(mutation):
    api = NativeSmokeApi()
    if mutation == "unaccepted":
        api.task["state"] = "failed"
    elif mutation == "stale":
        api.task["fence"] = 2
    elif mutation == "forged":
        api.artifacts[1]["provenance"]["origin"] = "worker-authored"
    elif mutation == "hash":
        api.artifact_bytes += b" "
    else:
        api.artifact_bytes = b'{"count":4,"sum":40,"mean":11}'
        api.artifacts[0]["sha256"] = hashlib.sha256(api.artifact_bytes).hexdigest()
    with pytest.raises(SMOKE["LiveVerificationError"]):
        SMOKE["verify_live_artifact"](api, "b" * 32)


def test_live_timeout_cancels_owned_team_without_retry(monkeypatch):
    api = NativeSmokeApi()
    api.final_state = "running"
    moments = iter([0, 200])
    monkeypatch.setitem(
        SMOKE["run_live_task"].__globals__,
        "time",
        SimpleNamespace(monotonic=lambda: next(moments)),
    )
    with pytest.raises(SMOKE["LiveVerificationError"], match="timed out"):
        SMOKE["run_live_task"](api, "fixture-api")
    assert api.teams["b" * 32]["state"] == "canceled"


@pytest.mark.parametrize("enabled", ["false", "true"])
def test_cli_workflow_inputs_never_enter_the_shell_or_enable_live_by_default(
    monkeypatch, capsys, enabled
):
    calls = []

    def record_run(installer, report, **kwargs):
        calls.append(kwargs)
        return {"status": "offline-recording"}

    monkeypatch.setitem(SMOKE["main"].__globals__, "run", record_run)
    monkeypatch.setattr(
        SMOKE["sys"],
        "argv",
        [
            "verify_swarm_install.py",
            "--installer",
            "fixture",
            "--report",
            "fixture.json",
            "--live-from-environment",
        ],
    )
    monkeypatch.setenv("SWARM_INSTALL_LIVE_ENABLED", enabled)
    monkeypatch.setenv("SWARM_INSTALL_LIVE_PROVIDER", "fixture-api")
    monkeypatch.setenv("SWARM_INSTALL_LIVE_MODEL", "fixture-model")
    SMOKE["main"]()
    assert calls == [
        {
            "live_provider": "fixture-api" if enabled == "true" else "",
            "live_model": "fixture-model" if enabled == "true" else "",
        }
    ]
    assert "offline-recording" in capsys.readouterr().out


def test_cli_suppresses_exception_bodies_and_chains(monkeypatch, capsys):
    def fail_run(*args, **kwargs):
        raise RuntimeError("untrusted-provider-body")

    monkeypatch.setitem(SMOKE["main"].__globals__, "run", fail_run)
    monkeypatch.setattr(
        SMOKE["sys"],
        "argv",
        [
            "verify_swarm_install.py",
            "--installer",
            "fixture",
            "--report",
            "fixture.json",
        ],
    )
    with pytest.raises(SystemExit) as failure:
        SMOKE["main"]()
    assert failure.value.code == 1
    stderr = capsys.readouterr().err
    assert "RuntimeError" in stderr and "untrusted-provider-body" not in stderr


@pytest.mark.parametrize("target", ["windows", "macos", "linux"])
def test_workflow_live_key_is_step_local_manual_only_and_default_off(target):
    workflow = yaml.safe_load((ROOT / ".github/workflows/desktop-installers.yml").read_text())
    trigger = workflow.get("on", workflow.get(True))
    assert trigger["workflow_dispatch"]["inputs"]["live_provider_verification"]["default"] is False
    job = workflow["jobs"][target]
    assert "SWARM_INSTALL_TEST_KEY" not in workflow.get("env", {})
    assert "SWARM_INSTALL_TEST_KEY" not in job.get("env", {})
    uses = [step for step in job["steps"] if "SWARM_INSTALL_TEST_KEY" in step.get("env", {})]
    assert len(uses) == 1
    step = uses[0]
    assert "--live-from-environment" in step["run"]
    assert "inputs.live_provider" not in step["run"]
    secret = step["env"]["SWARM_INSTALL_TEST_KEY"]
    assert "github.event_name == 'workflow_dispatch'" in secret
    assert "inputs.live_provider_verification" in secret
    assert "|| ''" in secret
