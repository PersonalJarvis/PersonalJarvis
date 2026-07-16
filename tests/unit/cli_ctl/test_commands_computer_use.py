"""Tests for the curated computer-use commands (deep-dive H-09)."""
from __future__ import annotations

from typer.testing import CliRunner

from jarvis.cli_ctl.__main__ import app

runner = CliRunner()


def test_start_requires_yes(capture_api):
    res = runner.invoke(app, ["computer-use", "start", "open the browser"])
    assert res.exit_code == 1
    assert capture_api["calls"] == []


def test_start_with_yes(capture_api):
    res = runner.invoke(
        app, ["computer-use", "start", "open the browser", "--yes"],
    )
    assert res.exit_code == 0
    call = capture_api["calls"][-1]
    assert call["method"] == "POST"
    assert call["path"] == "/api/computer-use/goals"
    assert call["body"]["goal"] == "open the browser"
    assert call["body"]["timeout_s"] == 120.0


def test_start_custom_timeout(capture_api):
    runner.invoke(
        app,
        ["computer-use", "start", "x", "--timeout-s", "300", "--yes"],
    )
    assert capture_api["calls"][-1]["body"]["timeout_s"] == 300.0


def test_list(capture_api):
    runner.invoke(app, ["computer-use", "list", "--limit", "5"])
    call = capture_api["calls"][-1]
    assert call["method"] == "GET"
    assert call["path"] == "/api/computer-use/goals"
    assert int(call["query"]["limit"]) == 5


def test_show(capture_api):
    runner.invoke(app, ["computer-use", "show", "abc123"])
    call = capture_api["calls"][-1]
    assert call["method"] == "GET"
    assert call["path"] == "/api/computer-use/goals/abc123"


def test_cancel_requires_yes(capture_api):
    assert runner.invoke(app, ["computer-use", "cancel", "abc123"]).exit_code == 1
    assert capture_api["calls"] == []


def test_cancel_with_yes(capture_api):
    res = runner.invoke(app, ["computer-use", "cancel", "abc123", "--yes"])
    assert res.exit_code == 0
    call = capture_api["calls"][-1]
    assert call["method"] == "POST"
    assert call["path"] == "/api/computer-use/goals/abc123/cancel"


def test_cancel_all_with_yes(capture_api):
    res = runner.invoke(app, ["computer-use", "cancel-all", "--yes"])
    assert res.exit_code == 0
    call = capture_api["calls"][-1]
    assert call["method"] == "POST"
    assert call["path"] == "/api/computer-use/goals/cancel-all"
