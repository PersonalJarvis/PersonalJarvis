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
    res = runner.invoke(app, ["tasks", "create", "--json-body", spec, "--yes"])
    assert res.exit_code == 0
    assert "new" in res.stdout


def test_create_rejects_invalid_json(mock_api, monkeypatch):
    monkeypatch.setenv("JARVISCTL_CONTROL_KEY", "jctl_x")
    res = runner.invoke(app, ["tasks", "create", "--json-body", "{not json", "--yes"])
    assert res.exit_code == 2  # usage error, no HTTP call made


def test_cancel_and_delete(mock_api, monkeypatch):
    monkeypatch.setenv("JARVISCTL_CONTROL_KEY", "jctl_x")
    mock_api[("POST", "/api/tasks/x/cancel")] = (200, {"ok": True})
    mock_api[("DELETE", "/api/tasks/x")] = (200, {"ok": True})
    assert runner.invoke(app, ["tasks", "cancel", "x", "--yes"]).exit_code == 0
    assert runner.invoke(app, ["tasks", "delete", "x", "--yes"]).exit_code == 0


def test_delete_fails_closed_without_yes(mock_api, monkeypatch):
    """A destructive curated command refuses non-interactively without --yes."""
    monkeypatch.setenv("JARVISCTL_CONTROL_KEY", "jctl_x")
    mock_api[("DELETE", "/api/tasks/x")] = (200, {"ok": True})
    res = runner.invoke(app, ["tasks", "delete", "x"])
    assert res.exit_code == 1  # gated: no --yes, non-interactive


def test_delete_dry_run_sends_nothing(mock_api, monkeypatch):
    monkeypatch.setenv("JARVISCTL_CONTROL_KEY", "jctl_x")
    # No route registered: if a request were sent it would 404 (exit 1).
    res = runner.invoke(app, ["--json", "tasks", "delete", "x", "--dry-run"])
    assert res.exit_code == 0
    assert "dry_run" in res.stdout
