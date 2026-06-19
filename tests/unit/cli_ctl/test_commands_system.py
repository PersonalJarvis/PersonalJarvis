import httpx
from typer.testing import CliRunner

from jarvis.cli_ctl.__main__ import app

runner = CliRunner()


def test_restart_posts_and_reports(mock_api, monkeypatch):
    monkeypatch.setenv("JARVISCTL_CONTROL_KEY", "jctl_x")
    mock_api[("POST", "/api/settings/restart-app")] = (200, {"ok": True, "restarting": True})
    res = runner.invoke(app, ["system", "restart"])
    assert res.exit_code == 0
    assert "restart" in res.stdout.lower()


def test_restart_on_headless_reports_clean_message(mock_api, monkeypatch):
    monkeypatch.setenv("JARVISCTL_CONTROL_KEY", "jctl_x")
    mock_api[("POST", "/api/settings/restart-app")] = (
        503, {"detail": "self-restart unavailable on this host"}
    )
    res = runner.invoke(app, ["system", "restart"])
    assert res.exit_code == 1
    assert "unavailable" in (res.stdout + res.stderr).lower()


def test_restart_refused_when_missions_running_hints_force(mock_api, monkeypatch):
    """A 409 from the mission guard prints the live missions + the --force hint."""
    monkeypatch.setenv("JARVISCTL_CONTROL_KEY", "jctl_x")
    mock_api[("POST", "/api/settings/restart-app")] = (
        409,
        {
            "detail": {
                "error": "missions_running",
                "missions": [
                    {"id": "019e-aaa", "title": "research US visa rules"},
                    {"id": "019e-bbb", "title": "build the dashboard"},
                ],
            }
        },
    )
    res = runner.invoke(app, ["system", "restart"])
    assert res.exit_code == 1
    out = (res.stdout + res.stderr).lower()
    assert "--force" in out
    assert "research us visa rules" in out  # the running mission is named


def test_restart_force_flag_sends_force_param(monkeypatch):
    """``--force`` propagates ``force=true`` so the guard lets the restart through."""
    monkeypatch.setenv("JARVISCTL_CONTROL_KEY", "jctl_x")
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["query"] = request.url.query.decode()
        return httpx.Response(200, json={"ok": True, "restarting": True})

    import jarvis.cli_ctl.client as client_mod

    real_init = client_mod.JarvisClient.__init__

    def patched_init(self, base_url, control_key, **kw):
        kw["transport"] = httpx.MockTransport(handler)
        real_init(self, base_url, control_key, **kw)

    monkeypatch.setattr(client_mod.JarvisClient, "__init__", patched_init)
    res = runner.invoke(app, ["system", "restart", "--force"])
    assert res.exit_code == 0
    assert "force=true" in seen["query"].lower()
