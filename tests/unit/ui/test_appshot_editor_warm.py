"""The appshot editor window is kept warm: prepared hidden, shown in place, hidden on close."""

from __future__ import annotations

import sys
from types import ModuleType

import pytest

from jarvis.ui.desktop_app import DesktopApp


class _Events:
    def __init__(self) -> None:
        self.loaded = _Hook()
        self.closed = _Hook()


class _Hook:
    def __init__(self) -> None:
        self.handlers: list = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self


class _Window:
    def __init__(self, *, page_ready: bool = True) -> None:
        self.page_ready = page_ready
        self.events = _Events()
        self.calls: list[str] = []

    def evaluate_js(self, js: str):
        self.calls.append("js")
        return self.page_ready

    def load_url(self, url: str) -> None:
        self.calls.append(f"load {url}")

    def show(self) -> None:
        self.calls.append("show")

    def hide(self) -> None:
        self.calls.append("hide")

    def destroy(self) -> None:
        self.calls.append("destroy")


def _app(monkeypatch: pytest.MonkeyPatch) -> DesktopApp:
    app = DesktopApp.__new__(DesktopApp)
    app._detached_windows = {}  # noqa: SLF001
    app._window = object()  # noqa: SLF001 - a live main window
    app._url = lambda: "http://127.0.0.1:47821"  # type: ignore[method-assign]  # noqa: SLF001
    app._window_background = lambda: "#000000"  # type: ignore[method-assign]  # noqa: SLF001
    app._arm_window_frame = lambda _w: None  # type: ignore[method-assign]  # noqa: SLF001
    monkeypatch.setattr("jarvis.ui.desktop_app._bring_window_to_front_by_title", lambda _t: True)
    return app


def test_prewarm_creates_the_editor_hidden_once(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[dict] = []

    def create_window(title, url, **kwargs):
        created.append({"title": title, "url": url, **kwargs})
        return _Window()

    monkeypatch.setitem(sys.modules, "webview", ModuleType("webview"))
    sys.modules["webview"].create_window = create_window  # type: ignore[attr-defined]
    app = _app(monkeypatch)

    first = app.prewarm_appshot_editor()
    second = app.prewarm_appshot_editor()

    assert first == {"ok": True, "already_open": False}
    assert second == {"ok": True, "already_open": True}
    assert len(created) == 1
    assert created[0]["hidden"] is True
    assert created[0]["url"].endswith("/?view=appshot-editor&solo=1")


def test_a_page_still_loading_is_navigated_then_shown(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _app(monkeypatch)
    window = _Window(page_ready=False)
    app._detached_windows["appshot-editor"] = window  # noqa: SLF001

    result = app.open_detached_window("appshot-editor", query="appshot=a1b2c3d4")

    assert result["ok"] is True
    assert window.calls == [
        "js",
        "load http://127.0.0.1:47821/?view=appshot-editor&solo=1&appshot=a1b2c3d4",
        "show",
    ]


def test_closing_the_editor_hides_it_and_keeps_it(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _app(monkeypatch)
    window = _Window()
    app._detached_windows["appshot-editor"] = window  # noqa: SLF001

    assert app.close_detached_window("appshot-editor") == {
        "ok": True,
        "view": "appshot-editor",
        "hidden": True,
    }
    assert window.calls == ["hide"]
    assert app._detached_windows["appshot-editor"] is window  # noqa: SLF001


def test_other_detached_views_still_close_for_real(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _app(monkeypatch)
    window = _Window()
    app._detached_windows["visualization"] = window  # noqa: SLF001

    assert app.close_detached_window("visualization")["ok"] is True
    assert window.calls == ["destroy"]


async def test_the_editor_window_is_prepared_through_the_registered_prewarmer() -> None:
    from jarvis.appshot import editor_window

    calls: list[str] = []
    editor_window.register_window_opener(
        lambda _q: {"ok": True}, prewarm=lambda: calls.append("warm") or {"ok": True}
    )
    try:
        await editor_window.prewarm_editor_window()
    finally:
        editor_window.register_window_opener(None)
    await editor_window.prewarm_editor_window()  # no shell: a quiet no-op
    assert calls == ["warm"]
