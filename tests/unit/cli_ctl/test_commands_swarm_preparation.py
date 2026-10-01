"""CLI preparation requires explicit writes and launches only saved references."""

import json

import pytest
from typer.testing import CliRunner

from jarvis.cli_ctl.__main__ import app

runner = CliRunner()
TEAM = "a" * 32


def test_prepare_creates_questions_without_starting_execution(capture_api, tmp_path):
    spec = tmp_path / "goal.json"
    spec.write_text(
        json.dumps(
            {"name": "Goal", "goal": "Compute a verified total", "request_key": "goal-once"}
        ),
        encoding="utf-8",
    )
    denied = runner.invoke(app, ["swarm", "prepare", str(spec)])
    assert denied.exit_code == 1
    assert capture_api["calls"] == []
    result = runner.invoke(app, ["swarm", "prepare", str(spec), "--yes"])
    assert result.exit_code == 0, result.output
    assert len(capture_api["calls"]) == 1
    assert capture_api["calls"][0]["path"] == "/api/swarm/preparations"
    assert capture_api["calls"][0]["body"]["preparation_required"] is True
    assert capture_api["calls"][0]["body"]["tasks"] == []


def test_preparation_read_is_read_only(capture_api):
    result = runner.invoke(app, ["swarm", "preparation", TEAM])
    assert result.exit_code == 0, result.output
    assert capture_api["calls"][0]["method"] == "GET"
    assert capture_api["calls"][0]["path"] == f"/api/swarm/teams/{TEAM}/preparation"


def test_plan_preserves_user_answers_and_revision(capture_api, tmp_path):
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps({"format": "JSON file"}), encoding="utf-8")
    args = [
        "swarm",
        "plan",
        TEAM,
        str(answers),
        "--expected-revision",
        "2",
        "--expected-storage-generation",
        "restored",
        "--request-key",
        "answer-once",
    ]
    assert runner.invoke(app, args).exit_code == 1
    assert capture_api["calls"] == []
    result = runner.invoke(app, [*args, "--yes"])
    assert result.exit_code == 0, result.output
    assert capture_api["calls"][0]["body"] == {
        "answers": {"format": "JSON file"},
        "expected_revision": 2,
        "expected_storage_generation": "restored",
        "request_key": "answer-once",
    }
    assert capture_api["calls"][0]["path"] == f"/api/swarm/teams/{TEAM}/preparation/answers"


@pytest.mark.parametrize("command", ["clarify", "launch"])
def test_mutation_requires_yes_and_keeps_the_saved_reference(capture_api, command):
    args = [
        "swarm",
        command,
        TEAM,
        "--expected-storage-generation",
        "restored",
        "--request-key",
        "once",
    ]
    expected = {"expected_storage_generation": "restored", "request_key": "once"}
    if command == "launch":
        args += ["--expected-revision", "3", "--digest", "b" * 64]
        expected.update(expected_revision=3, digest="b" * 64)
    assert runner.invoke(app, args).exit_code == 1
    assert capture_api["calls"] == []
    result = runner.invoke(app, [*args, "--yes"])
    assert result.exit_code == 0, result.output
    assert capture_api["calls"][0]["body"] == expected
    assert "tasks" not in expected


def test_launch_rejects_invalid_digest_without_http(capture_api):
    result = runner.invoke(
        app,
        [
            "swarm",
            "launch",
            TEAM,
            "--expected-storage-generation",
            "",
            "--expected-revision",
            "1",
            "--digest",
            "wrong",
            "--request-key",
            "once",
            "--yes",
        ],
    )
    assert result.exit_code != 0
    assert capture_api["calls"] == []
