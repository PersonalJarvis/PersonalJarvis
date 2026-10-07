"""Whole-window zoom reaches the WebView engine of the window that asked."""

import sys
import types
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.ui.web.desktop_routes import router
from jarvis.ui.window_zoom import ZOOM_MAX, ZOOM_MIN, clamp_zoom, set_window_zoom


class GtkWebView:
    def __init__(self) -> None:
        self.levels: list[float] = []

    def set_zoom_level(self, level: float) -> None:
        self.levels.append(level)


class CocoaWebView:
    def __init__(self, page_zoom: bool = True) -> None:
        self.page_zoom = page_zoom
        self.zooms: list[float] = []

    def respondsToSelector_(self, selector: str) -> bool:  # noqa: N802 — Objective-C name
        return self.page_zoom and selector == "setPageZoom:"

    def setPageZoom_(self, factor: float) -> None:  # noqa: N802 — Objective-C name
        self.zooms.append(factor)


def _install(monkeypatch: pytest.MonkeyPatch, name: str, module: types.ModuleType) -> None:
    monkeypatch.setitem(sys.modules, name, module)


def _platform(monkeypatch: pytest.MonkeyPatch, name: str, uid: str, view: object) -> None:
    module = types.ModuleType(name)
    module.BrowserView = type("BrowserView", (), {"instances": {uid: view}})
    _install(monkeypatch, name, module)


@pytest.fixture(autouse=True)
def _no_real_engines(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("webview.platforms.winforms", "webview.platforms.cocoa", "webview.platforms.gtk"):
        monkeypatch.delitem(sys.modules, name, raising=False)


def test_clamp_keeps_factors_in_range():
    assert clamp_zoom(1.1) == 1.1
    assert clamp_zoom(0.01) == ZOOM_MIN
    assert clamp_zoom(99) == ZOOM_MAX
    assert clamp_zoom(float("nan")) == 1.0


def test_no_window_and_no_engine():
    assert set_window_zoom(None, 1.2)["reason"] == "no_window"
    assert set_window_zoom(SimpleNamespace(uid="main"), 1.2)["reason"] == "zoom_unsupported"


def test_gtk_zoom_is_queued_on_the_main_loop(monkeypatch: pytest.MonkeyPatch):
    queued: list = []
    gi = types.ModuleType("gi")
    repository = types.ModuleType("gi.repository")
    repository.GLib = SimpleNamespace(idle_add=queued.append)
    gi.repository = repository
    _install(monkeypatch, "gi", gi)
    _install(monkeypatch, "gi.repository", repository)
    webview = GtkWebView()
    _platform(monkeypatch, "webview.platforms.gtk", "main", SimpleNamespace(webview=webview))

    assert set_window_zoom(SimpleNamespace(uid="main"), 1.25) == {"ok": True, "zoom": 1.25}
    assert webview.levels == []  # not on the caller's thread
    assert queued[0]() is False  # runs once
    assert webview.levels == [1.25]


def test_cocoa_zoom_needs_page_zoom(monkeypatch: pytest.MonkeyPatch):
    called: list = []
    helper = types.ModuleType("PyObjCTools")
    helper.AppHelper = SimpleNamespace(callAfter=lambda fn, arg: called.append((fn, arg)))
    _install(monkeypatch, "PyObjCTools", helper)
    webview = CocoaWebView()
    _platform(monkeypatch, "webview.platforms.cocoa", "main", SimpleNamespace(webview=webview))

    assert set_window_zoom(SimpleNamespace(uid="main"), 0.8)["ok"] is True
    fn, arg = called[0]
    fn(arg)
    assert webview.zooms == [0.8]

    old = CocoaWebView(page_zoom=False)
    _platform(monkeypatch, "webview.platforms.cocoa", "main", SimpleNamespace(webview=old))
    assert set_window_zoom(SimpleNamespace(uid="main"), 0.8)["reason"] == "zoom_unsupported"


def test_only_the_asking_window_is_zoomed(monkeypatch: pytest.MonkeyPatch):
    gi = types.ModuleType("gi")
    repository = types.ModuleType("gi.repository")
    repository.GLib = SimpleNamespace(idle_add=lambda fn: fn())
    gi.repository = repository
    _install(monkeypatch, "gi", gi)
    _install(monkeypatch, "gi.repository", repository)
    webview = GtkWebView()
    _platform(monkeypatch, "webview.platforms.gtk", "main", SimpleNamespace(webview=webview))

    assert set_window_zoom(SimpleNamespace(uid="detached"), 2.0)["reason"] == "zoom_unsupported"
    assert webview.levels == []


def test_engine_error_is_reported_not_raised(monkeypatch: pytest.MonkeyPatch):
    class Broken:
        def set_zoom_level(self, level: float) -> None:
            raise RuntimeError("engine gone")

    gi = types.ModuleType("gi")
    repository = types.ModuleType("gi.repository")
    repository.GLib = SimpleNamespace(idle_add=lambda fn: fn())
    gi.repository = repository
    _install(monkeypatch, "gi", gi)
    _install(monkeypatch, "gi.repository", repository)
    _platform(monkeypatch, "webview.platforms.gtk", "main", SimpleNamespace(webview=Broken()))

    assert set_window_zoom(SimpleNamespace(uid="main"), 1.5)["reason"] == "zoom_failed"


def test_route_without_and_with_a_desktop_shell():
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    denied = client.post("/api/window/zoom", json={"factor": 1.1}).json()
    assert denied["reason"] == "no_desktop_shell"
    assert client.post("/api/window/zoom", json={"factor": 50}).status_code == 422

    seen: list = []

    def zoom(factor: float, view: str | None) -> dict:
        seen.append((factor, view))
        return {"ok": True, "zoom": factor}

    app.state.desktop_app = SimpleNamespace(set_window_zoom=zoom)
    body = client.post("/api/window/zoom", json={"factor": 1.25, "view": "agentic-ide"}).json()
    assert body == {"ok": True, "zoom": 1.25}
    assert seen == [(1.25, "agentic-ide")]
    operation = app.openapi()["paths"]["/api/window/zoom"]["post"]
    assert operation["operationId"] == "zoom"


class WebView2:
    """Reading ZoomFactor off the UI thread is a COM error on the real control."""

    def __init__(self) -> None:
        self.on_ui_thread = False
        self._zoom = 1.0

    @property
    def ZoomFactor(self) -> float:  # noqa: N802 — .NET name
        if not self.on_ui_thread:
            raise RuntimeError("E_NOINTERFACE")
        return self._zoom

    @ZoomFactor.setter
    def ZoomFactor(self, value: float) -> None:  # noqa: N802 — .NET name
        if not self.on_ui_thread:
            raise RuntimeError("E_NOINTERFACE")
        self._zoom = value


class Form:
    InvokeRequired = True

    def __init__(self, webview: WebView2) -> None:
        self.webview = webview

    def Invoke(self, delegate) -> None:  # noqa: N802 — .NET name
        self.webview.on_ui_thread = True
        try:
            delegate()
        finally:
            self.webview.on_ui_thread = False


def test_webview2_zoom_runs_on_the_ui_thread(monkeypatch: pytest.MonkeyPatch):
    system = types.ModuleType("System")
    system.Type = object
    system.Func = {object: lambda fn: fn}
    _install(monkeypatch, "System", system)
    webview = WebView2()
    _platform(monkeypatch, "webview.platforms.winforms", "main", Form(webview))

    assert set_window_zoom(SimpleNamespace(uid="main"), 1.5) == {"ok": True, "zoom": 1.5}
    assert webview._zoom == 1.5
