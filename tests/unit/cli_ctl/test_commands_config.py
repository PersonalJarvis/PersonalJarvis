"""Tests for the config commands (self-mod + reply language)."""
from __future__ import annotations

from typer.testing import CliRunner

from jarvis.cli_ctl.__main__ import app

runner = CliRunner()


def test_get_sends_path_query(capture_api):
    res = runner.invoke(app, ["config", "get", "brain.primary"])
    assert res.exit_code == 0
    call = capture_api["calls"][-1]
    assert call["method"] == "GET" and call["path"] == "/api/control/config"
    assert call["query"]["path"] == "brain.primary"


def test_set_requires_yes(capture_api):
    res = runner.invoke(app, ["config", "set", "brain.primary", "openai"])
    assert res.exit_code == 1  # destructive: refused without --yes
    assert capture_api["calls"] == []


def test_set_with_yes_coerces_bool(capture_api):
    res = runner.invoke(app, ["config", "set", "x.flag", "true", "--yes"])
    assert res.exit_code == 0
    call = capture_api["calls"][-1]
    assert call["method"] == "PUT" and call["path"] == "/api/control/config"
    assert call["body"]["path"] == "x.flag"
    assert call["body"]["value"] is True


def test_set_keeps_string(capture_api):
    runner.invoke(app, ["config", "set", "brain.primary", "openai", "--yes"])
    assert capture_api["calls"][-1]["body"]["value"] == "openai"


def test_set_dry_run_sends_nothing(capture_api):
    res = runner.invoke(app, ["--json", "config", "set", "x", "1", "--dry-run"])
    assert res.exit_code == 0
    assert capture_api["calls"] == []
    assert "dry_run" in res.stdout


def test_list_allowlist(capture_api):
    runner.invoke(app, ["config", "list"])
    assert capture_api["calls"][-1]["path"] == "/api/control/allowlist"


def test_language_get(capture_api):
    res = runner.invoke(app, ["config", "language", "get"])
    assert res.exit_code == 0
    call = capture_api["calls"][-1]
    assert call["method"] == "GET" and call["path"] == "/api/settings/reply-language"


def test_language_set(capture_api):
    runner.invoke(app, ["config", "language", "set", "es"])
    call = capture_api["calls"][-1]
    assert call["method"] == "PUT" and call["path"] == "/api/settings/reply-language"
    assert call["body"] == {"language": "es", "persist": True}
