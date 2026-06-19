import json

from typer.testing import CliRunner

from jarvis.cli_ctl.__main__ import app

runner = CliRunner()


def test_version_runs_without_server():
    res = runner.invoke(app, ["version"])
    assert res.exit_code == 0
    assert "jarvisctl" in res.stdout


def test_login_stores_key_after_successful_probe(mock_api, tmp_path):
    mock_api[("GET", "/api/control/auth/probe")] = (200, {"ok": True})
    res = runner.invoke(
        app, ["auth", "login", "--url", "http://h:1", "--key", "jctl_x"]
    )
    assert res.exit_code == 0
    saved = json.loads(
        (tmp_path / "cfg" / "config.json").read_text(encoding="utf-8")
    )
    assert saved["control_key"] == "jctl_x"


def test_login_rejects_bad_key(mock_api):
    mock_api[("GET", "/api/control/auth/probe")] = (401, {"detail": "bad"})
    res = runner.invoke(
        app, ["auth", "login", "--url", "http://h:1", "--key", "wrong"]
    )
    assert res.exit_code == 1


def test_status_reports_reachability(mock_api):
    mock_api[("GET", "/api/control/auth/probe")] = (200, {"ok": True})
    res = runner.invoke(
        app, ["--json", "auth", "status", "--url", "http://h:1", "--key", "jctl_x"]
    )
    assert res.exit_code == 0
    assert json.loads(res.stdout)["reachable"] is True
