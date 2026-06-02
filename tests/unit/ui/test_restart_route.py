"""POST /api/settings/restart-app — one-click self-restart of the desktop app."""
from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.ui.web.settings_routes import router


def _client(desktop=None):
    app = FastAPI()
    app.include_router(router)
    app.state.config = SimpleNamespace(ui=SimpleNamespace())
    if desktop is not None:
        app.state.desktop_app = desktop
    return TestClient(app)


def test_restart_schedules_when_window_present():
    calls = {"n": 0}

    def request_restart():
        calls["n"] += 1
        return True

    r = _client(SimpleNamespace(request_restart=request_restart)).post(
        "/api/settings/restart-app"
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True, "restarting": True}
    assert calls["n"] == 1


def test_restart_503_without_desktop_app():
    r = _client().post("/api/settings/restart-app")  # headless: no desktop_app
    assert r.status_code == 503


def test_restart_503_when_no_window():
    # desktop present but request_restart returns False (headless / no window)
    desktop = SimpleNamespace(request_restart=lambda: False)
    r = _client(desktop).post("/api/settings/restart-app")
    assert r.status_code == 503
