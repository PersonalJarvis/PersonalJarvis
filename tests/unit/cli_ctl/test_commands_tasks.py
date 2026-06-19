import json

from typer.testing import CliRunner

from jarvis.cli_ctl.__main__ import app

runner = CliRunner()


def test_list_renders_rows(mock_api, monkeypatch):
    monkeypatch.setenv("JARVISCTL_CONTROL_KEY", "jctl_x")
    mock_api[("GET", "/api/tasks")] = (
        200, [{"id": "1", "state": "scheduled", "title": "t"}]
    )
    res = runner.invoke(app, ["tasks", "list"])
    assert res.exit_code == 0
    assert "scheduled" in res.stdout


def test_get_one(mock_api, monkeypatch):
    monkeypatch.setenv("JARVISCTL_CONTROL_KEY", "jctl_x")
    mock_api[("GET", "/api/tasks/abc")] = (200, {"id": "abc", "state": "running"})
    res = runner.invoke(app, ["--json", "tasks", "get", "abc"])
    assert res.exit_code == 0
    assert json.loads(res.stdout)["id"] == "abc"


def test_create_from_inline_json(mock_api, monkeypatch):
    monkeypatch.setenv("JARVISCTL_CONTROL_KEY", "jctl_x")
    mock_api[("POST", "/api/tasks")] = (201, {"id": "new", "state": "scheduled"})
    spec = json.dumps({
        "title": "remind me",
        "trigger": {"type": "after_delay", "delay_seconds": 60},
        "action": {"kind": "speak", "text": "hello"},
    })
    res = runner.invoke(app, ["tasks", "create", "--json-body", spec])
    assert res.exit_code == 0
    assert "new" in res.stdout


def test_create_rejects_invalid_json(mock_api, monkeypatch):
    monkeypatch.setenv("JARVISCTL_CONTROL_KEY", "jctl_x")
    res = runner.invoke(app, ["tasks", "create", "--json-body", "{not json"])
    assert res.exit_code == 2  # usage error, no HTTP call made


def test_cancel_and_delete(mock_api, monkeypatch):
    monkeypatch.setenv("JARVISCTL_CONTROL_KEY", "jctl_x")
    mock_api[("POST", "/api/tasks/x/cancel")] = (200, {"ok": True})
    mock_api[("DELETE", "/api/tasks/x")] = (200, {"ok": True})
    assert runner.invoke(app, ["tasks", "cancel", "x"]).exit_code == 0
    assert runner.invoke(app, ["tasks", "delete", "x"]).exit_code == 0
