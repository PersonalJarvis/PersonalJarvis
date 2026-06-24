"""Tests for the brain provider commands — the flagship switch surface."""
from __future__ import annotations

from typer.testing import CliRunner

from jarvis.cli_ctl.__main__ import app

runner = CliRunner()


def test_status_hits_providers(capture_api):
    res = runner.invoke(app, ["brain", "status"])
    assert res.exit_code == 0
    call = capture_api["calls"][-1]
    assert call["method"] == "GET" and call["path"] == "/api/providers"


def test_switch_sends_provider_and_persist(capture_api):
    res = runner.invoke(app, ["brain", "switch", "openai"])
    assert res.exit_code == 0
    call = capture_api["calls"][-1]
    assert call["method"] == "POST" and call["path"] == "/api/brain/switch"
    assert call["body"] == {"provider": "openai", "persist": True}


def test_switch_proceeds_without_yes(capture_api):
    # The flagship: a reversible provider switch must NOT require --yes.
    res = runner.invoke(app, ["brain", "switch", "openai"])
    assert res.exit_code == 0
    assert capture_api["calls"], "a request should have been sent"


def test_switch_no_persist(capture_api):
    runner.invoke(app, ["brain", "switch", "openai", "--no-persist"])
    assert capture_api["calls"][-1]["body"] == {"provider": "openai", "persist": False}


def test_switch_dry_run_sends_nothing(capture_api):
    res = runner.invoke(app, ["--json", "brain", "switch", "openai", "--dry-run"])
    assert res.exit_code == 0
    assert capture_api["calls"] == []
    assert "dry_run" in res.stdout


def test_subagent_switch(capture_api):
    runner.invoke(app, ["brain", "subagent-switch", "openai"])
    call = capture_api["calls"][-1]
    assert call["path"] == "/api/subagent/switch"
    assert call["body"]["provider"] == "openai"


def test_test_provider(capture_api):
    runner.invoke(app, ["brain", "test", "openai"])
    call = capture_api["calls"][-1]
    assert call["method"] == "POST" and call["path"] == "/api/providers/openai/test"


def test_deep_model(capture_api):
    runner.invoke(app, ["brain", "deep-model", "some-model", "--no-persist"])
    call = capture_api["calls"][-1]
    assert call["path"] == "/api/subagent/model"
    assert call["body"] == {"model": "some-model", "persist": False}
