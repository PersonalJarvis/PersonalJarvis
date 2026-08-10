"""Detached solo windows: lifecycle, quit semantics, and the /api/window routes.

The desktop shell can split a view into its own pywebview window
(``DesktopApp.open_detached_window``). These tests pin the contracts that keep
two windows from corrupting each other and the process from lingering:

- distinct title per detached view (the title IS the Win32 window handle),
- idempotent detach (second call focuses, never duplicates),
- reasoned ``ok: false`` answers instead of exceptions,
- the injection matrix (embedded flag always; broker token ONLY for the voice
  window; the single-use ``__JARVIS_TOKEN`` never re-offered),
- last-window-quit semantics (X on main with detached windows open must NOT
  quit; the final window sets the flag; teardown sweeps the registry),
- the routes' honest no-desktop-shell degradation and off-loop dispatch.

House patterns: ``DesktopApp.__new__`` + attribute setup (no real GUI), fake
``webview`` module via ``sys.modules`` (test_native_drag.py), recording
windows (test_desktop_show_window.py).
"""
from __future__ import annotations

import asyncio
import threading
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.ui.desktop_app import DETACHABLE_VIEWS, WINDOW_TITLE, DesktopApp
from jarvis.ui.web.desktop_routes import router as desktop_router


class _FakeWindow:
    def __init__(self, title: str = "", url: str = "") -> None:
        self.title = title
        self.url = url
        self.destroyed = False
        self.evaluated: list[str] = []
        self.events = SimpleNamespace(loaded=_List(), closed=_List(), closing=_List())

    def destroy(self) -> None:
        self.destroyed = True

    def evaluate_js(self, js: str) -> None:
        self.evaluated.append(js)


class _List(list):
    """Supports the ``events.closed += handler`` subscription idiom."""

    def __iadd__(self, handler: Any) -> _List:
        self.append(handler)
        return self


def _fake_webview(created: list[_FakeWindow], *, fail: bool = False) -> ModuleType:
    mod = ModuleType("webview")

    class WebViewException(Exception):
        pass

    def create_window(title: str, url: str, **_kwargs: Any) -> _FakeWindow:
        if fail:
            raise WebViewException("runtime window creation unsupported")
        window = _FakeWindow(title, url)
        created.append(window)
        return window

    mod.WebViewException = WebViewException  # type: ignore[attr-defined]
    mod.create_window = create_window  # type: ignore[attr-defined]
    return mod


def _app(monkeypatch: pytest.MonkeyPatch | None = None) -> DesktopApp:
    app = DesktopApp.__new__(DesktopApp)
    app.cfg = SimpleNamespace(
        ui=SimpleNamespace(dev_mode=False, admin_api_port=8781)
    )
    app._window = _FakeWindow(WINDOW_TITLE)
    app._window_visible = True
    app._detached_windows = {}
    app._user_requested_quit = False
    app._backend_loop = None
    app._server = None
    app.realtime_transport_broker_token = "broker-secret"  # noqa: S105
    app._window_background = lambda: "#101010"  # type: ignore[method-assign]
    app.published: list[tuple[str, bool]] = []
    app._publish_detached_event_threadsafe = (  # type: ignore[method-assign]
        lambda view, *, opened: app.published.append((view, opened))
    )
    if monkeypatch is not None:
        monkeypatch.setattr(
            "jarvis.ui.desktop_app._bring_window_to_front_by_title",
            lambda _title: True,
        )
        monkeypatch.setattr(
            "jarvis.ui.icon_utils.set_window_icon_by_title",
            lambda _title, _icon: True,
        )
        monkeypatch.setattr(
            "jarvis.ui.icon_utils.project_icon_path", lambda: "icon.ico"
        )
    return app


# --- open_detached_window ----------------------------------------------------


def test_open_detached_creates_titled_solo_window(monkeypatch) -> None:
    import sys

    app = _app(monkeypatch)
    created: list[_FakeWindow] = []
    monkeypatch.setitem(sys.modules, "webview", _fake_webview(created))

    result = app.open_detached_window("agentic-ide")

    assert result == {"ok": True, "already_open": False, "view": "agentic-ide"}
    assert len(created) == 1
    window = created[0]
    # Distinct title: FindWindowW-exact focus and the icon setter key on it.
    assert window.title == f"{WINDOW_TITLE} — Agents"
    assert window.title != WINDOW_TITLE
    assert "?view=agentic-ide&solo=1" in window.url
    assert app._detached_windows == {"agentic-ide": window}
    assert len(window.events.loaded) == 1  # injection hook
    assert len(window.events.closed) == 1  # deregistration hook
    assert app.published == [("agentic-ide", True)]


def test_open_detached_is_idempotent_and_focuses(monkeypatch) -> None:
    import sys

    app = _app(monkeypatch)
    created: list[_FakeWindow] = []
    monkeypatch.setitem(sys.modules, "webview", _fake_webview(created))
    focused: list[str] = []
    monkeypatch.setattr(
        "jarvis.ui.desktop_app._bring_window_to_front_by_title",
        lambda title: focused.append(title) or True,
    )

    app.open_detached_window("chats")
    result = app.open_detached_window("chats")

    assert result == {"ok": True, "already_open": True, "view": "chats"}
    assert len(created) == 1  # never a duplicate window
    assert focused == [f"{WINDOW_TITLE} — Voice"]


def test_open_detached_rejects_unknown_view(monkeypatch) -> None:
    app = _app(monkeypatch)
    assert app.open_detached_window("settings") == {
        "ok": False,
        "reason": "unknown_view",
    }


def test_open_detached_reports_backend_failure_with_fallback(monkeypatch) -> None:
    import sys

    app = _app(monkeypatch)
    monkeypatch.setitem(sys.modules, "webview", _fake_webview([], fail=True))

    result = app.open_detached_window("agentic-ide")

    assert result["ok"] is False
    assert "WebViewException" in result["reason"]
    assert result["fallback_url"] == "/?view=agentic-ide&solo=1"
    assert app._detached_windows == {}


def test_open_detached_without_any_live_window(monkeypatch) -> None:
    app = _app(monkeypatch)
    app._window = None

    result = app.open_detached_window("agentic-ide")

    assert result["ok"] is False
    assert result["reason"] == "no_live_window"
    assert result["fallback_url"] == "/?view=agentic-ide&solo=1"


# --- injection matrix --------------------------------------------------------


def test_voice_window_gets_broker_token_and_embedded_flag(monkeypatch) -> None:
    app = _app(monkeypatch)
    window = _FakeWindow()

    app._inject_into_secondary(window, "chats")

    js = "".join(window.evaluated)
    assert "__JARVIS_EMBEDDED_DESKTOP = true" in js
    assert "__JARVIS_REALTIME_BROKER_TOKEN" in js
    assert "broker-secret" in js
    assert "jarvis-token-ready" in js
    # The bootstrap token is single-use and already consumed by the main
    # window — re-offering it would 401 the cookie exchange.
    assert "__JARVIS_TOKEN =" not in js


def test_ide_window_gets_no_broker_token(monkeypatch) -> None:
    app = _app(monkeypatch)
    window = _FakeWindow()

    app._inject_into_secondary(window, "agentic-ide")

    js = "".join(window.evaluated)
    assert "__JARVIS_EMBEDDED_DESKTOP = true" in js
    assert "__JARVIS_REALTIME_BROKER_TOKEN" not in js
    assert "jarvis-token-ready" in js


# --- quit semantics ----------------------------------------------------------


def test_main_close_with_detached_window_does_not_quit(monkeypatch) -> None:
    app = _app(monkeypatch)
    app._detached_windows["chats"] = _FakeWindow()

    assert app._on_window_closing() is True  # destroy proceeds either way
    assert app._user_requested_quit is False  # ...but the process lives on


def test_main_close_without_detached_windows_quits(monkeypatch) -> None:
    app = _app(monkeypatch)

    assert app._on_window_closing() is True
    assert app._user_requested_quit is True  # 2026-07-01 mandate unchanged


def test_main_closed_hook_nulls_the_handle(monkeypatch) -> None:
    app = _app(monkeypatch)

    app._on_main_window_closed()

    assert app._window is None
    assert app._window_visible is False


def test_detached_close_with_main_alive_keeps_running(monkeypatch) -> None:
    app = _app(monkeypatch)
    app._detached_windows["chats"] = _FakeWindow()

    app._on_detached_closed("chats")

    assert app._detached_windows == {}
    assert app.published == [("chats", False)]
    assert app._user_requested_quit is False


def test_last_window_close_sets_the_quit_flag(monkeypatch) -> None:
    app = _app(monkeypatch)
    app._window = None  # main already closed earlier
    app._detached_windows["chats"] = _FakeWindow()

    app._on_detached_closed("chats")

    assert app._user_requested_quit is True  # webview.start() returns → quit


def test_destroy_all_windows_sweeps_detached_then_main(monkeypatch) -> None:
    app = _app(monkeypatch)
    main = app._window
    detached = _FakeWindow()
    app._detached_windows["agentic-ide"] = detached

    app._destroy_all_windows()

    assert detached.destroyed is True
    assert main.destroyed is True
    assert app._detached_windows == {}


# --- close_detached_window / snapshot ---------------------------------------


def test_close_detached_window_destroys_only_that_window(monkeypatch) -> None:
    app = _app(monkeypatch)
    window = _FakeWindow()
    app._detached_windows["chats"] = window

    result = app.close_detached_window("chats")

    assert result == {"ok": True, "view": "chats"}
    assert window.destroyed is True
    # Deregistration happens in the closed hook (same path as the window's X),
    # which the fake does not fire — the registry entry is still present here.
    assert "chats" in app._detached_windows


def test_close_detached_window_when_not_detached(monkeypatch) -> None:
    app = _app(monkeypatch)
    assert app.close_detached_window("chats") == {
        "ok": False,
        "reason": "not_detached",
    }


def test_snapshot_lists_detached_views(monkeypatch) -> None:
    app = _app(monkeypatch)
    app._detached_windows["chats"] = _FakeWindow()
    app._detached_windows["agentic-ide"] = _FakeWindow()

    assert app.detached_views_snapshot() == ["chats", "agentic-ide"]


# --- _ensure_main_window -----------------------------------------------------


def test_ensure_main_window_recreates_and_rehooks(monkeypatch) -> None:
    import sys

    app = _app(monkeypatch)
    app._window = None
    created: list[_FakeWindow] = []
    monkeypatch.setitem(sys.modules, "webview", _fake_webview(created))

    app._ensure_main_window()

    assert len(created) == 1
    assert created[0].title == WINDOW_TITLE
    assert app._window is created[0]
    assert app._window_visible is True
    assert len(created[0].events.closing) == 1  # quit contract re-attached
    assert len(created[0].events.closed) == 1  # null-out re-attached


def test_ensure_main_window_shows_existing_window(monkeypatch) -> None:
    app = _app(monkeypatch)
    shown: list[bool] = []
    app._safe_window_show = lambda: shown.append(True)  # type: ignore[method-assign]

    app._ensure_main_window()

    assert shown == [True]


def test_ensure_main_window_survives_create_failure(monkeypatch) -> None:
    import sys

    app = _app(monkeypatch)
    app._window = None
    monkeypatch.setitem(sys.modules, "webview", _fake_webview([], fail=True))

    app._ensure_main_window()  # must not raise — honest logged no-op

    assert app._window is None


# --- event publish (bus down) ------------------------------------------------


def test_publish_without_backend_loop_is_honest_noop() -> None:
    app = DesktopApp.__new__(DesktopApp)
    app._backend_loop = None
    app._server = None

    # Must not raise: during early boot/teardown there is no bus — and no
    # frontend listening either.
    app._publish_detached_event_threadsafe("chats", opened=True)


# --- registry constant -------------------------------------------------------


def test_detachable_views_have_distinct_titles_per_label() -> None:
    # Every view must produce a title distinct from the main window's, and no
    # two views that can be detached AT THE SAME TIME may share a title —
    # FindWindowW-by-title would focus/icon the wrong window. The coding family
    # shares one label deliberately: only one coding view can be live at once
    # (single-IDE rule), so their titles can never coexist.
    app = DesktopApp.__new__(DesktopApp)
    coding = {"agentic-ide", "agentic-ide-classic", "chat-workspace"}
    seen: dict[str, str] = {}
    for view in DETACHABLE_VIEWS:
        title = app._detached_title(view)
        assert title != WINDOW_TITLE
        assert title.startswith(WINDOW_TITLE)
        clash = seen.get(title)
        if clash is not None:
            assert view in coding and clash in coding, (
                f"'{view}' and '{clash}' share the title '{title}' but can be "
                "detached simultaneously"
            )
        seen[title] = view


def test_visualization_view_detaches_and_reattaches(monkeypatch) -> None:
    """The visual stage is a first-class detachable view.

    It is the section people put on a second monitor — a diagram is something
    you keep looking at WHILE working in the main window — so it must survive
    the whole window lifecycle: its own title, a registry entry, the open/close
    round trip, and the mirrored events that tell every other window about it.
    """
    import sys

    app = _app(monkeypatch)
    created: list[_FakeWindow] = []
    monkeypatch.setitem(sys.modules, "webview", _fake_webview(created))

    opened = app.open_detached_window("visualization")

    assert opened == {"ok": True, "already_open": False, "view": "visualization"}
    assert created[0].title == f"{WINDOW_TITLE} — Visualization"
    assert "?view=visualization&solo=1" in created[0].url
    assert app.detached_views_snapshot() == ["visualization"]
    assert app.published == [("visualization", True)]

    assert app.close_detached_window("visualization") == {
        "ok": True,
        "view": "visualization",
    }
    assert created[0].destroyed is True

    # The window's own X and the reattach route share one deregistration path.
    app._on_detached_closed("visualization")
    assert app.detached_views_snapshot() == []
    assert app.published[-1] == ("visualization", False)
    # Main is still alive, so the process must not arm the quit flag.
    assert app._user_requested_quit is False


def test_visualization_window_gets_no_broker_token(monkeypatch) -> None:
    """Only the voice surface may run a realtime broker.

    Two live brokers publish competing WebRTC offers, so the token is injected
    into the "chats" window and nowhere else. Pinned per detachable view rather
    than once, because every view added to the registry passes through the same
    injection hook.
    """
    app = _app(monkeypatch)
    window = _FakeWindow()

    app._inject_into_secondary(window, "visualization")

    js = "".join(window.evaluated)
    assert "__JARVIS_EMBEDDED_DESKTOP = true" in js
    assert "__JARVIS_REALTIME_BROKER_TOKEN" not in js
    assert "jarvis-token-ready" in js


# --- routes ------------------------------------------------------------------


def _client(desktop: Any | None) -> TestClient:
    api = FastAPI()
    api.include_router(desktop_router)
    if desktop is not None:
        api.state.desktop_app = desktop
    return TestClient(api)


def test_detach_route_without_desktop_shell_degrades_honestly() -> None:
    response = _client(None).post("/api/window/detach", json={"view": "chats"})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["reason"] == "no_desktop_shell"
    assert body["fallback_url"] == "/?view=chats&solo=1"


def test_detach_route_dispatches_off_the_event_loop() -> None:
    seen: dict[str, Any] = {}

    def open_detached_window(view: str) -> dict[str, Any]:
        seen["view"] = view
        try:
            asyncio.get_running_loop()
            seen["on_loop"] = True
        except RuntimeError:
            seen["on_loop"] = False  # correct: a plain worker thread
        seen["thread"] = threading.current_thread().name
        return {"ok": True, "already_open": False, "view": view}

    desktop = SimpleNamespace(open_detached_window=open_detached_window)
    response = _client(desktop).post(
        "/api/window/detach", json={"view": "agentic-ide"}
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert seen["view"] == "agentic-ide"
    assert seen["on_loop"] is False


def test_detach_route_surfaces_handler_failure_with_fallback() -> None:
    def open_detached_window(_view: str) -> dict[str, Any]:
        raise RuntimeError("boom")

    desktop = SimpleNamespace(open_detached_window=open_detached_window)
    response = _client(desktop).post("/api/window/detach", json={"view": "chats"})

    body = response.json()
    assert body["ok"] is False
    assert "RuntimeError" in body["reason"]
    assert body["fallback_url"] == "/?view=chats&solo=1"


def test_detached_route_snapshot_and_headless_empty() -> None:
    assert _client(None).get("/api/window/detached").json() == {
        "ok": True,
        "views": [],
    }
    desktop = SimpleNamespace(detached_views_snapshot=lambda: ["chats"])
    assert _client(desktop).get("/api/window/detached").json() == {
        "ok": True,
        "views": ["chats"],
    }


def test_reattach_route_shapes() -> None:
    assert _client(None).post(
        "/api/window/reattach", json={"view": "chats"}
    ).json() == {"ok": False, "reason": "no_desktop_shell"}

    desktop = SimpleNamespace(
        close_detached_window=lambda view: {"ok": True, "view": view}
    )
    assert _client(desktop).post(
        "/api/window/reattach", json={"view": "chats"}
    ).json() == {"ok": True, "view": "chats"}
