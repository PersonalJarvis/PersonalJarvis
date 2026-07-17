"""M5: the tray must degrade to a logged no-op (not a silently dying daemon
thread) on a box without a graphical display / notification-area host, and its
menu strings must be English (Output-Language Policy).

Seam-level: display_present is forced via monkeypatch — proven on this Windows
host without a real Linux/headless session.
"""
from __future__ import annotations

import logging
import sys
import threading
import types
from pathlib import Path

from jarvis.ui import tray as tray_mod
from jarvis.ui.tray import JarvisTray


def test_tray_start_is_noop_without_display(monkeypatch, caplog) -> None:
    # Pinned to linux: the display gate sits after the darwin branch, so on a
    # macOS dev host the test would otherwise assert the wrong path.
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setattr(tray_mod, "display_present", lambda: False, raising=False)
    # If the gate works, _run is never reached; the no-op keeps RED from launching
    # a real pystray icon.
    monkeypatch.setattr(JarvisTray, "_run", lambda self: None)
    t = JarvisTray()
    with caplog.at_level(logging.INFO):
        t.start()
    assert t._thread is None  # gated: no tray thread spawned
    assert "tray not started" in caplog.text.lower()


class _FakeDetachedIcon:
    """Records the ctor kwargs + run_detached()/stop() calls."""

    def __init__(self, name: str, **kwargs) -> None:
        self.name = name
        self.kwargs = kwargs
        self.run_detached_calls = 0
        self.stop_calls = 0

    def run_detached(self) -> None:
        self.run_detached_calls += 1

    def stop(self) -> None:
        self.stop_calls += 1


class _FakeMenu:
    SEPARATOR = object()

    def __init__(self, *items) -> None:
        self.items = items


def _install_fake_darwin_modules(monkeypatch, icon_cls) -> object:
    """Injects fake pystray + AppKit modules; returns the fake NSApplication."""
    fake_pystray = types.SimpleNamespace(
        Icon=icon_cls,
        Menu=_FakeMenu,
        MenuItem=lambda *args, **kwargs: (args, kwargs),
    )
    monkeypatch.setitem(sys.modules, "pystray", fake_pystray)
    nsapp = object()
    fake_appkit = types.SimpleNamespace(
        NSApplication=types.SimpleNamespace(sharedApplication=lambda: nsapp),
    )
    monkeypatch.setitem(sys.modules, "AppKit", fake_appkit)
    # Synchronous callAfter: runs the marshaled fn immediately, so the tests
    # observe the mutation without a live AppKit run loop.
    fake_pyobjctools = types.SimpleNamespace(
        AppHelper=types.SimpleNamespace(callAfter=lambda fn, *args: fn(*args)),
    )
    monkeypatch.setitem(sys.modules, "PyObjCTools", fake_pyobjctools)
    return nsapp


def test_tray_start_is_noop_on_macos_off_main_thread(monkeypatch, caplog) -> None:
    # BUG-056: AppKit allows UI objects (the NSStatusItem behind pystray's
    # darwin backend) on the main thread ONLY; created from a worker thread
    # the process dies with a native, uncatchable AppKit assertion — the
    # first real-Mac boot aborted exactly there ("Python quit unexpectedly").
    # An off-main start() must stay a logged no-op (the tray floor used by
    # TrayOnlySurface, whose start() runs on the backend worker thread).
    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setattr(tray_mod, "display_present", lambda: True, raising=False)
    monkeypatch.setattr(
        JarvisTray, "_run", lambda self: (_ for _ in ()).throw(AssertionError)
    )
    # Pretend the caller is NOT the main thread (deterministic, no worker
    # thread needed for the assertion).
    fake_main = threading.Thread(name="fake-main")
    monkeypatch.setattr(threading, "main_thread", lambda: fake_main)
    t = JarvisTray()
    with caplog.at_level(logging.INFO):
        t.start()
    assert t._thread is None  # gated: no tray thread spawned
    assert t._icon is None  # no icon built off-main either
    assert "tray not started" in caplog.text.lower()
    assert "main thread" in caplog.text.lower()


def test_tray_start_on_macos_main_thread_runs_detached(monkeypatch) -> None:
    # BUG-056 follow-up: a MAIN-thread start() on macOS hosts a real menu-bar
    # icon — pystray.Icon built with the shared NSApplication
    # (darwin_nsapplication=...) + run_detached(); no jarvis-tray worker
    # thread is ever spawned.
    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setattr(tray_mod, "display_present", lambda: True, raising=False)
    monkeypatch.setattr(tray_mod, "_make_icon", lambda state, size=64: object())
    nsapp = _install_fake_darwin_modules(monkeypatch, _FakeDetachedIcon)
    t = JarvisTray()
    t.start()
    assert t._thread is None  # no jarvis-tray worker thread on darwin
    icon = t._icon
    assert isinstance(icon, _FakeDetachedIcon)
    assert icon.kwargs["darwin_nsapplication"] is nsapp
    assert icon.run_detached_calls == 1
    assert t._darwin_detached is True
    # stop() must reach the detached icon through the main-thread marshal.
    t.stop()
    assert icon.stop_calls == 1
    assert t._icon is None
    assert t._darwin_detached is False


def test_tray_start_on_macos_main_thread_degrades_when_icon_ctor_raises(
    monkeypatch, caplog
) -> None:
    # AD-6: a broken menu-bar host (Icon ctor raising) must degrade to a
    # logged no-op — never an exception out of start().
    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setattr(tray_mod, "display_present", lambda: True, raising=False)
    monkeypatch.setattr(tray_mod, "_make_icon", lambda state, size=64: object())

    class _BoomIcon:
        def __init__(self, *args, **kwargs) -> None:
            raise RuntimeError("no status bar host")

    _install_fake_darwin_modules(monkeypatch, _BoomIcon)
    t = JarvisTray()
    with caplog.at_level(logging.INFO):
        t.start()  # must not raise
    assert t._thread is None
    assert t._icon is None
    assert t._darwin_detached is False
    assert "tray not started" in caplog.text.lower()


def test_tray_start_spawns_thread_with_display(monkeypatch) -> None:
    # AD-7: with a display present (Windows/Linux-X11) the tray still starts.
    # (macOS is gated separately — see the darwin tests above.) Pinned to
    # win32 so the test asserts the same path on every dev/CI host.
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setattr(tray_mod, "display_present", lambda: True, raising=False)
    ran: list[bool] = []
    monkeypatch.setattr(JarvisTray, "_run", lambda self: ran.append(True))
    t = JarvisTray()
    t.start()
    if t._thread is not None:
        t._thread.join(timeout=2)
    assert ran == [True]


def test_tray_menu_strings_are_english() -> None:
    src = Path(tray_mod.__file__).read_text(encoding="utf-8")
    for german in (
        '"Öffnen"',
        '"Pausieren"',
        '"Fortsetzen"',
        '"Beenden"',
        '"Notfall-Stop"',
        '"Config neu laden"',
    ):
        assert german not in src, german
    for english in (
        '"Open"',
        '"Pause"',
        '"Resume"',
        '"Quit"',
        '"Emergency stop"',
        '"Reload config"',
    ):
        assert english in src, english
