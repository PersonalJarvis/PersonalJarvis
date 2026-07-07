import httpx
from typer.testing import CliRunner

from jarvis.cli_ctl.__main__ import app

runner = CliRunner()


def test_restart_posts_and_reports(mock_api, monkeypatch):
    monkeypatch.setenv("JARVISCTL_CONTROL_KEY", "jctl_x")
    mock_api[("POST", "/api/settings/restart-app")] = (200, {"ok": True, "restarting": True})
    res = runner.invoke(app, ["system", "restart", "--yes"])
    assert res.exit_code == 0
    assert "restart" in res.stdout.lower()


def test_restart_fails_closed_without_yes(mock_api, monkeypatch):
    """restart is destructive; non-interactive without --yes refuses early."""
    monkeypatch.setenv("JARVISCTL_CONTROL_KEY", "jctl_x")
    mock_api[("POST", "/api/settings/restart-app")] = (200, {"ok": True})
    res = runner.invoke(app, ["system", "restart"])
    assert res.exit_code == 1


def test_restart_on_headless_reports_clean_message(mock_api, monkeypatch):
    monkeypatch.setenv("JARVISCTL_CONTROL_KEY", "jctl_x")
    mock_api[("POST", "/api/settings/restart-app")] = (
        503, {"detail": "self-restart unavailable on this host"}
    )
    res = runner.invoke(app, ["system", "restart", "--yes"])
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
    res = runner.invoke(app, ["system", "restart", "--yes"])
    assert res.exit_code == 1
    out = (res.stdout + res.stderr).lower()
    assert "--force" in out
    assert "research us visa rules" in out  # the running mission is named


def test_audio_devices_lists_without_options(mock_api, monkeypatch):
    monkeypatch.setenv("JARVISCTL_CONTROL_KEY", "jctl_x")
    mock_api[("GET", "/api/settings/audio-devices")] = (
        200,
        {
            "available": True,
            "outputs": [{"name": "PRO X Gaming Headset", "is_default": True}],
            "inputs": [{"name": "Microphone (PRO X)", "is_default": True}],
            "selected_output": "auto-headset",
            "selected_input": "auto-headset",
        },
    )
    res = runner.invoke(app, ["system", "audio-devices"])
    assert res.exit_code == 0
    assert "pro x" in res.stdout.lower()


def test_audio_devices_puts_picked_devices(monkeypatch):
    """--output/--input send a PUT with the picked names (reversible, no --yes)."""
    monkeypatch.setenv("JARVISCTL_CONTROL_KEY", "jctl_x")
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["body"] = request.read().decode()
        return httpx.Response(
            200,
            json={
                "ok": True,
                "selected_output": "PRO X Gaming Headset",
                "selected_input": "auto-headset",
                "persisted": True,
                "applied_live": True,
                "restart_required": False,
            },
        )

    import jarvis.cli_ctl.client as client_mod

    real_init = client_mod.JarvisClient.__init__

    def patched_init(self, base_url, control_key, **kw):
        kw["transport"] = httpx.MockTransport(handler)
        real_init(self, base_url, control_key, **kw)

    monkeypatch.setattr(client_mod.JarvisClient, "__init__", patched_init)
    res = runner.invoke(
        app,
        ["system", "audio-devices", "--output", "PRO X Gaming Headset"],
    )
    assert res.exit_code == 0
    assert seen["method"] == "PUT"
    assert seen["path"] == "/api/settings/audio-devices"
    assert "PRO X Gaming Headset" in str(seen["body"])
    assert "input_device" not in str(seen["body"])


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
    res = runner.invoke(app, ["system", "restart", "--force", "--yes"])
    assert res.exit_code == 0
    assert "force=true" in seen["query"].lower()
