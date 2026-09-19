"""Curated Swarm commands route exact inputs and require explicit mutation approval."""

import json

import pytest
from typer.testing import CliRunner

from jarvis.cli_ctl.__main__ import app
from jarvis.cli_ctl.reserved import is_control_invocation

runner = CliRunner()
TEAM_ID = "a" * 32


def test_swarm_invocation_is_reserved_for_the_control_cli():
    assert is_control_invocation(["swarm", "list"])
    assert is_control_invocation(["--json", "swarm", "list"])


@pytest.mark.parametrize("action", ["start", "pause", "resume", "stop", "cancel", "archive"])
def test_mutations_require_yes_and_preserve_version(capture_api, action):
    result = runner.invoke(app, ["swarm", action, TEAM_ID])
    assert result.exit_code == 1, result.output
    assert capture_api["calls"] == []
    result = runner.invoke(app, ["swarm", action, TEAM_ID, "--yes", "--expected-version", "7"])
    assert result.exit_code == 0, result.output
    call = capture_api["calls"][-1]
    assert call["method"] == "POST"
    assert call["path"] == f"/api/swarm/teams/{TEAM_ID}/{action}"
    assert call["body"] == {"expected_version": 7}


@pytest.mark.parametrize(
    "action", ["start", "pause", "resume", "stop", "cancel", "archive", "delete"]
)
@pytest.mark.parametrize("generation", ["", "restored-generation"])
def test_lifecycle_preserves_explicit_storage_generation(capture_api, action, generation):
    args = [
        "swarm",
        action,
        TEAM_ID,
        "--expected-storage-generation",
        generation,
        "--expected-version",
        "7",
        "--yes",
    ]
    if action == "delete":
        args.extend(["--request-key", "delete-selected"])
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    assert capture_api["calls"][-1]["body"]["expected_storage_generation"] == generation
    assert capture_api["calls"][-1]["body"]["expected_version"] == 7


def test_lifecycle_refuses_oversized_storage_generation_before_http(capture_api):
    result = runner.invoke(
        app,
        [
            "swarm",
            "start",
            TEAM_ID,
            "--expected-storage-generation",
            "g" * 65,
            "--yes",
        ],
    )
    assert result.exit_code != 0
    assert capture_api["calls"] == []


def test_read_commands_use_selected_team_and_bounded_page(capture_api):
    result = runner.invoke(app, ["swarm", "list", "--limit", "25", "--offset", "50"])
    assert result.exit_code == 0, result.output
    assert capture_api["calls"][-1]["query"] == {"limit": "25", "offset": "50"}
    result = runner.invoke(app, ["swarm", "show", TEAM_ID])
    assert result.exit_code == 0, result.output
    assert capture_api["calls"][-1]["path"] == f"/api/swarm/teams/{TEAM_ID}"
    result = runner.invoke(app, ["swarm", "world", TEAM_ID, "--group", "research"])
    assert result.exit_code == 0, result.output
    assert capture_api["calls"][-1]["query"] == {"group": "research"}
    result = runner.invoke(app, ["swarm", "records", TEAM_ID, "artifacts", "--limit", "12"])
    assert result.exit_code == 0, result.output
    assert capture_api["calls"][-1]["path"] == f"/api/swarm/teams/{TEAM_ID}/artifacts"
    assert capture_api["calls"][-1]["query"] == {"limit": "12", "offset": "0"}


def test_create_file_keeps_exact_counters_and_requires_yes(capture_api, tmp_path):
    spec = tmp_path / "team.json"
    budget = "90071992547409931234567890"
    spec.write_text(
        json.dumps(
            {
                "name": "Research",
                "goal": "Use selected inputs",
                "request_key": "explicit-create",
                "limits": {"token_budget": budget},
            }
        ),
        encoding="utf-8-sig",
    )
    result = runner.invoke(app, ["swarm", "create", str(spec)])
    assert result.exit_code == 1, result.output
    assert capture_api["calls"] == []
    result = runner.invoke(app, ["swarm", "create", str(spec), "--yes"])
    assert result.exit_code == 0, result.output
    call = capture_api["calls"][-1]
    assert call["path"] == "/api/swarm/teams"
    assert call["body"]["limits"]["token_budget"] == budget
    assert call["body"]["request_key"] == "explicit-create"
    assert len(capture_api["calls"]) == 1


def test_dry_run_never_starts_work(capture_api):
    result = runner.invoke(app, ["swarm", "start", TEAM_ID, "--dry-run"])
    assert result.exit_code == 0, result.output
    assert capture_api["calls"] == []


@pytest.mark.parametrize(
    "arguments",
    [
        ["show", "../outside"],
        ["start", "A" * 32, "--yes"],
        ["list", "--limit", "201"],
        ["records", TEAM_ID, "secrets"],
        ["records", TEAM_ID, "tasks", "--offset", "10000001"],
    ],
)
def test_invalid_identity_page_or_kind_never_reaches_http(capture_api, arguments):
    result = runner.invoke(app, ["swarm", *arguments])
    assert result.exit_code != 0
    assert capture_api["calls"] == []


def test_invalid_creation_never_echoes_rejected_private_input(capture_api, tmp_path):
    spec = tmp_path / "bad.json"
    spec.write_text(
        json.dumps(
            {
                "name": "Research",
                "goal": "Goal",
                "request_key": "key",
                "unselected_private_input": "private-canary-not-for-output",
            }
        ),
        encoding="utf-8",
    )
    result = runner.invoke(app, ["swarm", "create", str(spec), "--yes"])
    assert result.exit_code != 0
    assert "private-canary-not-for-output" not in result.output
    assert capture_api["calls"] == []
    spec.write_bytes(b" " * 1048577)
    result = runner.invoke(app, ["swarm", "create", str(spec), "--yes"])
    assert result.exit_code != 0
    assert capture_api["calls"] == []
