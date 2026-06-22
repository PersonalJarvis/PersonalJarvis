"""Tests for the sessions/chats commands."""
from __future__ import annotations

from typer.testing import CliRunner

from jarvis.cli_ctl.__main__ import app

runner = CliRunner()


def test_list(capture_api):
    runner.invoke(app, ["sessions", "list"])
    assert capture_api["calls"][-1]["path"] == "/api/chats"


def test_show(capture_api):
    runner.invoke(app, ["sessions", "show", "text", "c1"])
    assert capture_api["calls"][-1]["path"] == "/api/chats/text/c1"


def test_delete_requires_yes(capture_api):
    assert runner.invoke(app, ["sessions", "delete", "c1"]).exit_code == 1
    assert capture_api["calls"] == []


def test_delete_with_yes(capture_api):
    res = runner.invoke(app, ["sessions", "delete", "c1", "--yes"])
    assert res.exit_code == 0
    call = capture_api["calls"][-1]
    assert call["method"] == "DELETE" and call["path"] == "/api/chats/text/c1"


def test_resume_proceeds_without_yes(capture_api):
    res = runner.invoke(app, ["sessions", "resume", "text", "c1"])
    assert res.exit_code == 0
    assert capture_api["calls"][-1]["path"] == "/api/chats/text/c1/resume"
