"""Tests for the missions commands."""
from __future__ import annotations

from typer.testing import CliRunner

from jarvis.cli_ctl.__main__ import app

runner = CliRunner()


def test_list_with_state(capture_api):
    runner.invoke(app, ["missions", "list", "--state", "RUNNING"])
    call = capture_api["calls"][-1]
    assert call["path"] == "/api/missions" and call["query"]["state"] == "RUNNING"


def test_show(capture_api):
    runner.invoke(app, ["missions", "show", "m1"])
    assert capture_api["calls"][-1]["path"] == "/api/missions/m1"


def test_result(capture_api):
    runner.invoke(app, ["missions", "result", "m1"])
    call = capture_api["calls"][-1]
    assert call["method"] == "GET"
    assert call["path"] == "/api/missions/m1/result"


def test_tool_approvals(capture_api):
    runner.invoke(app, ["missions", "tool-approvals", "m1"])
    call = capture_api["calls"][-1]
    assert call["method"] == "GET"
    assert call["path"] == "/api/missions/m1/tool-approvals"


def test_approve_tool_requires_yes(capture_api):
    result = runner.invoke(app, ["missions", "approve-tool", "m1", "t1"])
    assert result.exit_code == 1
    assert capture_api["calls"] == []


def test_approve_tool_with_yes(capture_api):
    result = runner.invoke(
        app,
        ["missions", "approve-tool", "m1", "t1", "--yes"],
    )
    assert result.exit_code == 0
    call = capture_api["calls"][-1]
    assert call["method"] == "POST"
    assert call["path"] == "/api/missions/m1/tool-approvals/t1/approve"


def test_deny_tool_sends_audit_reason(capture_api):
    result = runner.invoke(
        app,
        ["missions", "deny-tool", "m1", "t1", "--reason", "not-now"],
    )
    assert result.exit_code == 0
    call = capture_api["calls"][-1]
    assert call["path"] == "/api/missions/m1/tool-approvals/t1/deny"
    assert call["body"] == {"reason": "not-now"}


def test_dispatch_requires_yes(capture_api):
    res = runner.invoke(app, ["missions", "dispatch", "do a thing"])
    assert res.exit_code == 1
    assert capture_api["calls"] == []


def test_dispatch_with_yes(capture_api):
    res = runner.invoke(app, ["missions", "dispatch", "do a thing", "--yes"])
    assert res.exit_code == 0
    call = capture_api["calls"][-1]
    assert call["method"] == "POST" and call["path"] == "/api/missions/dispatch"
    assert call["body"]["prompt"] == "do a thing"
    assert call["body"]["language"] == "en"


def test_cancel_requires_yes(capture_api):
    assert runner.invoke(app, ["missions", "cancel", "m1"]).exit_code == 1


def test_cancel_with_yes(capture_api):
    res = runner.invoke(app, ["missions", "cancel", "m1", "--yes"])
    assert res.exit_code == 0
    assert capture_api["calls"][-1]["path"] == "/api/missions/m1/cancel"


def test_kill_with_yes(capture_api):
    runner.invoke(app, ["missions", "kill", "w1", "--yes"])
    assert capture_api["calls"][-1]["path"] == "/api/missions/kill/w1"
