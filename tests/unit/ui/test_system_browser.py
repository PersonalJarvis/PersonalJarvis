"""The system browser dock never owns the user's browser process/profile."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.ui import system_browser
from jarvis.ui.system_browser import BrowserWindow, SystemBrowserSession, Viewport
from jarvis.ui.web.system_browser_routes import router

BOUNDS = Viewport(100, 80, 800, 600, 1200, 800)


class FakeNative:
    def __init__(self):
        self.valid = True
        self.moves = []
        self.restores = []
        self.stops = 0
        self.fail = False
        self.window = BrowserWindow(42, 7, "Example - Chrome")

    def windows(self, executable):
        return [self.window]

    def matches(self, window, executable):
        return self.valid and window == self.window

    def snapshot(self, window):
        return "original-placement"

    def watch(self, callback):
        self.callback = callback
        return SimpleNamespace(stop=self.stop)

    def stop(self):
        self.stops += 1

    def place(self, window, bounds, *, raise_window):
        if self.fail:
            raise OSError("position failed")
        self.moves.append((window, bounds, raise_window))

    def restore(self, window, placement):
        self.restores.append((window, placement))


@pytest.fixture
def dock():
    api = FakeNative()
    now = [10.0]
    session = SystemBrowserSession(
        object(), native=api, executable=Path("chrome.exe"), clock=lambda: now[0],
    )
    token = session.windows()["windows"][0]["id"]
    return session, api, now, token


def test_window_ids_are_opaque_and_selection_is_explicit(dock):
    session, api, _, token = dock
    assert token != str(api.window.handle)
    assert session.attach("42", BOUNDS)["reason"] == "window_gone"
    assert not api.moves
    assert session.attach(token, BOUNDS)["ok"]
    assert api.moves[-1][2] is True


def test_detach_restores_once_without_closing_browser(dock):
    session, api, _, token = dock
    lease = session.attach(token, BOUNDS)["lease"]
    assert session.detach("stale")["ok"] is False
    assert not api.restores
    assert session.detach(lease)["ok"]
    session.close()
    assert api.restores == [(api.window, "original-placement")]
    assert api.stops == 1


def test_lease_expires_after_lost_frontend(dock):
    session, api, now, token = dock
    session.attach(token, BOUNDS)
    now[0] += system_browser.LEASE_SECONDS + 1
    api.callback("lease")
    assert not session.status()["docked"]
    assert len(api.restores) == 1


def test_stale_updates_cannot_renew_or_detach_a_new_session(dock):
    session, api, now, token = dock
    first = session.attach(token, BOUNDS)["lease"]
    session.detach(first)
    second = session.attach(token, BOUNDS)["lease"]
    assert first != second
    now[0] += 2
    assert session.present(first, BOUNDS)["ok"] is False
    assert session.detach(first)["ok"] is False
    assert session.present(second, BOUNDS)["ok"]
    assert len(api.restores) == 1


def test_host_events_reposition_without_focus_theft_and_restore_on_close(dock):
    session, api, _, token = dock
    session.attach(token, BOUNDS)
    api.callback("host_moved")
    assert api.moves[-1][2] is False
    api.callback("host_focused")
    assert api.moves[-1][2] is True
    api.callback("host_gone")
    assert len(api.restores) == 1


def test_closed_browser_is_not_restored_or_replaced(dock):
    session, api, _, token = dock
    lease = session.attach(token, BOUNDS)["lease"]
    api.valid = False
    assert session.present(lease, BOUNDS)["reason"] == "window_gone"
    assert api.restores == []
    assert api.stops == 1


def test_failed_attach_restores_and_stops_observer(dock):
    session, api, _, token = dock
    api.fail = True
    with pytest.raises(OSError):
        session.attach(token, BOUNDS)
    assert not session.status()["docked"]
    assert len(api.restores) == 1
    assert api.stops == 1


def test_new_window_inventory_invalidates_old_selection(dock):
    session, api, _, token = dock
    session.windows()
    assert session.attach(token, BOUNDS)["ok"] is False
    assert not api.moves


def test_host_event_cannot_move_a_recycled_browser_handle(dock):
    session, api, _, token = dock
    session.attach(token, BOUNDS)
    api.valid = False
    api.callback("host_focused")
    assert len(api.moves) == 1
    assert not session.status()["docked"]


def test_existing_session_is_not_silently_replaced(dock):
    session, api, _, token = dock
    session.attach(token, BOUNDS)
    assert session.attach(token, BOUNDS)["reason"] == "already_docked"
    assert not api.restores


def test_launch_uses_only_normal_browser_executable(monkeypatch, dock):
    session, _, _, _ = dock
    calls = []
    def launch(args, **kwargs):
        calls.append((args, kwargs))
    monkeypatch.setattr(system_browser.subprocess, "Popen", launch)
    assert session.open()["ok"]
    assert calls[0][0] == ["chrome.exe"]
    assert calls[0][1]["creationflags"] == system_browser.NO_WINDOW_CREATIONFLAGS
    assert calls[0][1]["encoding"] == "utf-8"


def test_viewport_scales_for_dpi_zoom_and_negative_monitor_coordinates():
    assert BOUNDS.pixels((-1920, 100), (1800, 1200)) == (-1770, 220, 1200, 900)


@pytest.mark.parametrize("values", [(-1, 0, 800, 600, 1200, 800),
                                   (0, 0, 1300, 600, 1200, 800),
                                   (0, 0, 800, float("nan"), 1200, 800),
                                   (0, 0, 800, 600, 0, 800)])
def test_invalid_viewport_never_reaches_native_window(values):
    with pytest.raises(ValueError):
        Viewport(*values)


def test_headless_routes_degrade_honestly_and_are_cli_discoverable():
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        status = client.get("/api/system-browser/status").json()
        assert not status["available"] and not status["can_dock"]
        assert client.post("/api/system-browser/open").json()["ok"] is False
        assert client.get("/api/system-browser/windows").json()["windows"] == []
    operations = [op for path in app.openapi()["paths"].values() for op in path.values()]
    assert len(operations) == 6
    assert len({op["operationId"] for op in operations}) == 6
    assert all(op["tags"] == ["system-browser"] and op["summary"] for op in operations)


def test_routes_reject_offscreen_bounds_before_dispatch():
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        response = client.post("/api/system-browser/attach", json={"window_id": "x", "bounds": {
            "x": 100, "y": 0, "width": 1200, "height": 600,
            "viewport_width": 1200, "viewport_height": 800,
        }})
    assert response.status_code == 422


@pytest.mark.parametrize("fail_second_hook", [False, True])
def test_observer_unhooks_and_joins_on_stop_or_partial_start_failure(fail_second_hook):
    import ctypes
    import queue
    from ctypes import wintypes

    from jarvis.ui.system_browser_win32 import _Watch

    class FakeUser32:
        def __init__(self):
            self.messages = queue.Queue()
            self.hooks = []
            self.unhooked = []
            self.timers_stopped = []

        def PeekMessageW(self, *args):
            return 0

        def SetWinEventHook(self, *args):
            if fail_second_hook and self.hooks:
                return 0
            handle = len(self.hooks) + 1
            self.hooks.append(handle)
            return handle

        def UnhookWinEvent(self, handle):
            self.unhooked.append(handle)
            return True

        def SetTimer(self, *args):
            return 5

        def KillTimer(self, _window, timer):
            self.timers_stopped.append(timer)

        def PostThreadMessageW(self, _thread, message, *_args):
            self.messages.put(message)
            return True

        def GetMessageW(self, msg, *_args):
            message = self.messages.get(timeout=2)
            msg._obj.message = message
            return 0 if message == 0x0012 else 1

    user = FakeUser32()
    api = SimpleNamespace(
        w=wintypes, u=user, host=1,
        c=SimpleNamespace(byref=ctypes.byref, WinError=lambda _: OSError("hook failed"),
                          get_last_error=lambda: 5),
        k=SimpleNamespace(GetCurrentThreadId=lambda: 12), hook_type=lambda fn: fn,
    )
    if fail_second_hook:
        with pytest.raises(OSError):
            _Watch(api, lambda _: None)
        assert user.unhooked == [1]
    else:
        watcher = _Watch(api, lambda _: None)
        watcher.stop()
        watcher.stop()
        assert not watcher.thread.is_alive()
        assert user.unhooked == [1, 2]
        assert user.timers_stopped == [5]
