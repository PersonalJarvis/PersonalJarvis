"""Caption buttons call the same window methods the frame used to."""

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.ui.web.desktop_routes import router
from jarvis.ui.window_command import run_window_command, window_chrome


class Window:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def minimize(self) -> None:
        self.calls.append("minimize")

    def maximize(self) -> None:
        self.calls.append("maximize")

    def restore(self) -> None:
        self.calls.append("restore")

    def destroy(self) -> None:
        self.calls.append("destroy")


def test_chrome_side_follows_the_platform():
    assert window_chrome("darwin")["controls"] == "leading"
    assert window_chrome("win32")["controls"] == "trailing"
    assert window_chrome("linux")["controls"] == "trailing"
    assert window_chrome("win32")["frameless"] is False


def test_maximize_toggles_and_close_destroys():
    window = Window()
    assert run_window_command(window, "maximize")["maximized"] is True
    assert run_window_command(window, "maximize")["maximized"] is False
    assert run_window_command(window, "maximize", maximized=True)["maximized"] is False
    assert run_window_command(window, "minimize") == {"ok": True, "maximized": False}
    assert run_window_command(window, "close")["ok"] is True
    assert window.calls == ["maximize", "restore", "restore", "minimize", "destroy"]


def test_missing_window_and_unknown_action():
    assert run_window_command(None, "close")["reason"] == "no_window"
    window = Window()
    assert run_window_command(window, "explode")["reason"] == "unknown_action"
    assert window.calls == []


def test_routes_without_a_desktop_shell():
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    assert client.get("/api/window/chrome").json()["controls"] == "none"
    denied = client.post("/api/window/command", json={"action": "minimize"}).json()
    assert denied["reason"] == "no_desktop_shell"
    window = Window()
    app.state.desktop_app = SimpleNamespace(
        window_chrome_snapshot=lambda: {**window_chrome("win32"), "frameless": True},
        window_command=lambda action, view: run_window_command(window, action),
    )
    assert client.get("/api/window/chrome").json()["frameless"] is True
    assert client.post("/api/window/command", json={"action": "close"}).json()["ok"] is True
    assert window.calls == ["destroy"]
    operation = app.openapi()["paths"]["/api/window/command"]["post"]
    assert operation["operationId"] == "command"
    assert operation["tags"] == ["window"]
