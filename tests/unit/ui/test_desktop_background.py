"""Background lifecycle contracts with fake native surfaces, not physical OS proof."""

from __future__ import annotations

import asyncio
import sys
import threading
from types import SimpleNamespace

import pytest

from jarvis.ui.desktop_app import DesktopApp
from jarvis.ui.desktop_background import BackgroundSession


class Event:
    def __init__(self, ready=False):
        self.handlers = []
        self.ready = ready

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def is_set(self):
        return self.ready

    def wait(self, timeout=None):
        return self.ready

    def fire(self):
        self.ready = True
        for handler in self.handlers:
            handler()


class Window:
    def __init__(self, renderer="edgechromium", *, ready=True):
        self.gui = SimpleNamespace(renderer=renderer)
        self.events = SimpleNamespace(
            shown=Event(ready), closed=Event(), closing=Event(), loaded=Event(),
        )
        self.hidden = False
        self.destroyed = False
        self.evaluated = []

    def hide(self):
        self.hidden = True

    def show(self):
        self.hidden = False

    def restore(self):
        self.hidden = False

    def destroy(self):
        self.events.closing.fire()
        self.destroyed = True
        self.events.closed.fire()

    def evaluate_js(self, script):
        self.evaluated.append(script)


class Tray:
    ready = True

    def __init__(self):
        self.modes = []
        self.errors = []

    def set_background_mode(self, mode):
        self.modes.append(mode)

    def set_error(self, message):
        self.errors.append(message)


@pytest.fixture
def rig(monkeypatch):
    app = DesktopApp.__new__(DesktopApp)
    app._background = BackgroundSession()
    app._webview_persistent_profile = True
    app._window = Window()
    app._window_visible = True
    app._detached_windows = {}
    app._shutdown_done = False
    app._user_requested_quit = False
    app._tray = Tray()
    app._blank_watchdog = None
    app._background_created = []
    app._publish_detached_event_threadsafe = lambda *args, **kwargs: None
    app._window_background = lambda: "#000000"
    app._url = lambda: "http://localhost:47869"
    app._arm_blank_window_watchdog = lambda window: None
    app._restore_overlay_for_visible_window = lambda: None
    app._reload_window_if_stale = lambda: None
    app._safe_window_show = lambda: None
    app._arm_force_exit = lambda **kwargs: None
    app.realtime_transport_broker_token = "test-broker"  # noqa: S105 - inert token-replay fixture

    def create_window(title, url=None, **kwargs):
        window = Window()
        app._background_created.append((window, title, url, kwargs))
        return window

    monkeypatch.setitem(sys.modules, "webview", SimpleNamespace(create_window=create_window))
    monkeypatch.setattr("jarvis.ui.desktop_app._bring_window_to_front_by_title", lambda title: True)
    monkeypatch.setattr("jarvis.ui.icon_utils.set_window_icon_by_title", lambda *args: True)
    monkeypatch.setattr("jarvis.ui.icon_utils.project_icon_path", lambda: "icon.ico")
    return app


async def enable(app):
    return await asyncio.to_thread(app.set_background_mode, True)


async def test_opt_in_destroys_real_window_keeps_same_backend_and_reopens(rig):
    original = rig._window
    backend = rig._server = object()
    rig._hook_main_window_lifecycle()
    assert rig.get_background_status()["enabled"] is False
    assert (await enable(rig))["enabled"] is True
    keeper, _, url, kwargs = rig._background_created[0]
    assert url is None
    assert kwargs["hidden"] is True
    assert "script" not in kwargs["html"]
    assert "jarvis" not in kwargs["html"].lower()
    assert keeper.evaluated == []
    assert not keeper.events.loaded.handlers

    original.destroy()

    assert original.destroyed and not original.hidden
    assert rig._window is None
    assert rig._server is backend
    assert not rig._user_requested_quit
    assert rig.get_background_status()["mode"] == "background"
    assert rig._tray.modes[-1] == "background"
    assert not keeper.destroyed

    result = await asyncio.to_thread(rig._focus_window_now)
    assert result == {"ok": True, "focused": True}
    assert rig._window is not original
    assert rig._server is backend
    assert rig.get_background_status()["mode"] == "foreground"


def test_default_close_still_quits(rig):
    assert rig._on_window_closing() is True
    assert rig._user_requested_quit is True
    assert rig._background_created == []


@pytest.mark.parametrize("renderer", ["edgechromium", "wkwebview", "qtwebengine"])
async def test_shared_background_path_with_fake_gui_capabilities(rig, renderer):
    rig._window.gui.renderer = renderer
    assert (await enable(rig))["enabled"] is True


@pytest.mark.parametrize(
    "reason", ["tray_unavailable", "hidden_window_unavailable", "native_window_not_ready"],
)
async def test_unavailable_never_arms_or_creates_keeper(rig, reason):
    if reason == "tray_unavailable":
        rig._tray.ready = False
    elif reason == "hidden_window_unavailable":
        rig._window.gui.renderer = "gtkwebkit2"
    else:
        rig._window.events.shown.ready = False
    status = await enable(rig)
    assert not status["available"] and not status["enabled"]
    assert status["reason"] == reason
    assert rig._background_created == []


@pytest.mark.parametrize("persistent", [False, None])
async def test_private_or_unknown_profile_refuses_keeper_without_changing_auth(rig, persistent):
    rig._webview_persistent_profile = persistent
    status = await enable(rig)
    assert status["reason"] == "persistent_profile_unavailable"
    assert not status["available"] and not status["enabled"]
    assert rig._background_created == []


async def test_enable_is_idempotent_and_disabling_restores_main_first(rig):
    await enable(rig)
    await enable(rig)
    assert len(rig._background_created) == 1
    keeper = rig._background.keeper
    rig._window = None

    status = await asyncio.to_thread(rig.set_background_mode, False)

    assert rig._window is not None
    assert keeper.destroyed
    assert not status["enabled"]
    assert not rig._user_requested_quit


async def test_reopen_failure_preserves_background_control(rig):
    await enable(rig)
    rig._window = None
    keeper = rig._background.keeper
    rig._ensure_main_window = lambda: None
    status = await asyncio.to_thread(rig.set_background_mode, False)
    assert status["enabled"] and status["reason"] == "reopen_failed"
    assert not keeper.destroyed


async def test_failed_keeper_readiness_does_not_change_close_policy(rig, monkeypatch):
    keeper = Window(ready=False)
    monkeypatch.setitem(
        sys.modules, "webview", SimpleNamespace(create_window=lambda *a, **k: keeper),
    )
    status = await enable(rig)
    assert status["reason"] == "hidden_window_not_ready"
    assert not status["enabled"] and keeper.destroyed
    assert rig._on_window_closing() is True
    assert rig._user_requested_quit


async def test_last_detached_close_respects_opt_in(rig):
    await enable(rig)
    rig._window = None
    rig._detached_windows["chats"] = Window()
    rig._on_detached_closed("chats")
    assert not rig._user_requested_quit
    assert rig.get_background_status()["mode"] == "background"


async def test_explicit_quit_destroys_keeper_even_with_no_visible_window(rig, monkeypatch):
    await enable(rig)
    keeper = rig._background.keeper
    rig._window = None
    done = threading.Event()

    def sequence(*, set_quit, destroy_window):
        set_quit()
        destroy_window()
        done.set()

    monkeypatch.setattr("jarvis.ui.relauncher.run_restart_quit_sequence", sequence)
    assert rig.request_quit()
    assert await asyncio.to_thread(done.wait, 2.0)
    assert keeper.destroyed and rig._user_requested_quit
    assert not rig.get_background_status()["enabled"]


async def test_tray_loss_restores_real_control_surface(rig):
    await enable(rig)
    keeper = rig._background.keeper
    rig._window = None
    rig._tray.ready = False
    await asyncio.to_thread(rig._background_control_lost)
    assert rig._window is not None
    assert keeper.destroyed
    assert not rig._user_requested_quit


async def test_tray_loss_with_failed_reopen_ends_hidden_execution(rig):
    await enable(rig)
    keeper = rig._background.keeper
    rig._window = None
    rig._tray.ready = False
    rig._ensure_main_window = lambda: None
    await asyncio.to_thread(rig._background_control_lost)
    assert rig._user_requested_quit and keeper.destroyed


async def test_idle_bridge_restores_control_without_any_client_or_status_request(rig, monkeypatch):
    import queue

    from jarvis.ui.tray import TrayCommand

    class BridgeTray(Tray):
        def __init__(self):
            super().__init__()
            self._command_queue = queue.Queue()

        def start(self):
            pass  # The fake has no native thread; the real bridge still runs.

        def set_state(self, state):
            pass  # Voice activity does not affect this lifecycle fixture.

    await enable(rig)
    keeper = rig._background.keeper
    rig._window = None
    tray = BridgeTray()
    monkeypatch.setattr("jarvis.ui.tray.JarvisTray", lambda: tray)
    restored = threading.Event()
    restore = rig._background_control_lost

    def on_loss():
        restore()
        restored.set()

    rig._background_control_lost = on_loss
    rig._start_tray_and_bridge()
    try:
        # No frontend, GET/status read, or tray command reports the loss.
        tray.ready = False
        assert await asyncio.to_thread(restored.wait, 2.0)
        assert keeper.destroyed and rig._window is not None
        assert not rig._background.enabled and not rig._user_requested_quit
        restored_window = rig._window
        await asyncio.to_thread(restore)
        assert rig._window is restored_window

        # A recovered tray does not silently re-arm the session's close policy.
        tray.ready = True
        assert not rig._background.enabled
        assert (await enable(rig))["enabled"]
    finally:
        rig._shutdown_done = True
        tray._command_queue.put(TrayCommand(action="test_shutdown"))
        await asyncio.to_thread(rig._tray_bridge_thread.join, 2.0)
        rig._destroy_background_keeper()
    assert not rig._tray_bridge_thread.is_alive()


async def test_backend_death_with_only_keeper_exits_instead_of_ghosting(rig):
    await enable(rig)
    keeper = rig._background.keeper
    rig._window = None
    destroyed = threading.Event()
    original_destroy = rig._destroy_all_windows

    def destroy():
        original_destroy()
        destroyed.set()

    rig._destroy_all_windows = destroy
    rig._note_backend_stopped("test failure")
    assert await asyncio.to_thread(destroyed.wait, 2.0)
    assert keeper.destroyed and rig._user_requested_quit
    assert rig._tray.errors == ["Backend stopped"]


def test_reopened_main_does_not_replay_one_time_auth_token(rig):
    window = Window()
    rig._inject_reopened_main(window)
    script = "".join(window.evaluated)
    assert "__JARVIS_EMBEDDED_DESKTOP" in script
    assert "__JARVIS_REALTIME_BROKER_TOKEN" in script
    assert "__JARVIS_TOKEN =" not in script


async def test_quit_racing_enable_cannot_leave_keeper(rig, monkeypatch):
    keeper = Window()

    def create(*args, **kwargs):
        rig._user_requested_quit = True
        return keeper

    monkeypatch.setitem(sys.modules, "webview", SimpleNamespace(create_window=create))
    status = await enable(rig)
    assert not status["enabled"] and status["mode"] == "stopping"
    assert keeper.destroyed


async def test_disable_during_window_close_preserves_keeper(rig):
    await enable(rig)
    keeper = rig._background.keeper
    assert rig._on_window_closing()
    result = await asyncio.to_thread(rig.set_background_mode, False)
    assert result["reason"] == "window_closing"
    assert result["enabled"] and not keeper.destroyed


async def test_disable_waits_for_native_reopened_window(rig):
    await enable(rig)
    keeper = rig._background.keeper
    rig._window = None

    def pending_reopen():
        rig._window = Window(ready=False)

    rig._ensure_main_window = pending_reopen
    result = await asyncio.to_thread(rig.set_background_mode, False)
    assert result["reason"] == "reopen_failed"
    assert result["enabled"] and not keeper.destroyed
