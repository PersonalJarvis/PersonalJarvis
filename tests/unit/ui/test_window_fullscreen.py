"""Fullscreen transitions preserve the native window's restore semantics."""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.ui.web.desktop_routes import router
from jarvis.ui.window_fullscreen import set_window_fullscreen


class Window:
    def __init__(self):
        self.calls = 0
        self.fail = False

    def toggle_fullscreen(self):
        if self.fail:
            raise RuntimeError("window unavailable")
        self.calls += 1


def test_idempotent_entry_and_restoration():
    window = Window()
    for enabled in [False, True, True, False, False]:
        assert set_window_fullscreen(window, enabled) == {"ok": True, "fullscreen": enabled}
    assert window.calls == 2


def test_failed_transition_is_retryable():
    window = Window()
    window.fail = True
    with pytest.raises(RuntimeError):
        set_window_fullscreen(window, True)
    window.fail = False
    assert set_window_fullscreen(window, True)["ok"]
    assert window.calls == 1


def test_headless_degrades():
    assert not set_window_fullscreen(None, True)["ok"]


def test_route_and_cli_metadata():
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    assert (
        client.post("/api/window/fullscreen", json={"enabled": True}).json()["reason"]
        == "no_desktop_shell"
    )
    window = Window()
    app.state.desktop_app = SimpleNamespace(
        set_fullscreen=lambda enabled: set_window_fullscreen(window, enabled)
    )
    assert client.post("/api/window/fullscreen", json={"enabled": True}).json()["fullscreen"]
    assert (
        client.post("/api/window/fullscreen", json={"enabled": False}).json()["fullscreen"] is False
    )
    operation = app.openapi()["paths"]["/api/window/fullscreen"]["post"]
    assert operation["operationId"] == "fullscreen"
    assert operation["tags"] == ["window"]
