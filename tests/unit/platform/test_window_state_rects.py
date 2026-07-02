"""Cross-platform window identity + frame rect — every OS is first-class.

The multi-monitor Computer-Use fix stands on reading the TARGET WINDOW's
frame rect in the platform's input units on ALL three platforms:

* Windows — DWM extended frame bounds by hwnd (existing, DPI-pinned caller),
* macOS   — Quartz ``CGWindowListCopyWindowInfo`` bounds (global points,
  top-left origin: the SAME space Quartz mouse events and mss rects use),
* Linux/X11 — ``xdotool`` geometry by window id (root pixels); Wayland
  degrades to ``None`` (callers fall back to monitor scope + the engine's
  clear X11/XWayland message).

All native backends are faked — these tests run on any host.
"""
from __future__ import annotations

import subprocess
import sys
import types

from jarvis.platform import window_state as ws
from jarvis.platform.window_state import WindowInfo

# ---------------------------------------------------------------------------
# macOS (fake Quartz)
# ---------------------------------------------------------------------------

def _fake_quartz(entries: list[dict]) -> types.ModuleType:
    mod = types.ModuleType("Quartz")
    mod.kCGWindowListOptionOnScreenOnly = 1 << 0
    mod.kCGWindowListExcludeDesktopElements = 1 << 4
    mod.kCGNullWindowID = 0
    mod.CGWindowListCopyWindowInfo = lambda options, relative_to: entries
    return mod


_MAIL_WINDOW = {
    "kCGWindowNumber": 7,
    "kCGWindowLayer": 0,
    "kCGWindowBounds": {"X": 1728.0, "Y": -200.0,
                        "Width": 1200.0, "Height": 800.0},
    "kCGWindowName": "Inbox",
    "kCGWindowOwnerName": "Mail",
}
_OVERLAY = {
    "kCGWindowNumber": 3,
    "kCGWindowLayer": 25,  # status-bar layer — never a capture target
    "kCGWindowBounds": {"X": 0.0, "Y": 0.0, "Width": 3456.0, "Height": 24.0},
    "kCGWindowName": "Item-0",
    "kCGWindowOwnerName": "SystemUIServer",
}


def test_macos_frame_rect_resolves_by_window_number(monkeypatch):
    monkeypatch.setattr(ws, "detect_platform", lambda: "darwin")
    monkeypatch.setitem(
        sys.modules, "Quartz", _fake_quartz([_OVERLAY, _MAIL_WINDOW]),
    )
    rect = ws.window_frame_rect(WindowInfo(title="Inbox", handle=7))
    assert rect == (1728, -200, 1200, 800)


def test_macos_foreground_window_is_first_normal_layer_window(monkeypatch):
    monkeypatch.setattr(ws, "detect_platform", lambda: "darwin")
    monkeypatch.setitem(
        sys.modules, "Quartz", _fake_quartz([_OVERLAY, _MAIL_WINDOW]),
    )
    win = ws.foreground_window()
    assert win is not None
    assert win.handle == 7
    assert win.title == "Inbox"


def test_macos_without_quartz_degrades_to_title_only(monkeypatch):
    monkeypatch.setattr(ws, "detect_platform", lambda: "darwin")
    monkeypatch.setitem(sys.modules, "Quartz", None)  # import -> ImportError
    monkeypatch.setattr(ws, "get_foreground_title", lambda: "Inbox")
    win = ws.foreground_window()
    assert win == WindowInfo(title="Inbox")
    assert ws.window_frame_rect(WindowInfo(title="Inbox", handle=7)) is None


# ---------------------------------------------------------------------------
# Linux / X11 (fake xdotool)
# ---------------------------------------------------------------------------

def _wire_x11(monkeypatch, responses: dict[str, str]) -> list[list[str]]:
    """Fake ``subprocess.run`` keyed by the xdotool subcommand."""
    calls: list[list[str]] = []
    monkeypatch.setattr(ws, "detect_platform", lambda: "linux")
    monkeypatch.setattr(ws, "is_wayland", lambda: False)
    monkeypatch.setattr(ws, "display_present", lambda: True)
    monkeypatch.setattr(ws.shutil, "which", lambda name: f"/usr/bin/{name}")

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        key = cmd[1] if len(cmd) > 1 else ""
        out = responses.get(key)
        if out is None:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")

    monkeypatch.setattr(ws.subprocess, "run", fake_run)
    return calls


def test_linux_frame_rect_resolves_by_window_id(monkeypatch):
    calls = _wire_x11(monkeypatch, {
        "getwindowgeometry":
            "WINDOW=123456\nX=2400\nY=300\nWIDTH=1000\nHEIGHT=700\nSCREEN=0\n",
    })
    rect = ws.window_frame_rect(WindowInfo(title="Firefox", handle=123456))
    assert rect == (2400, 300, 1000, 700)
    assert any("123456" in c for call in calls for c in call)


def test_linux_foreground_window_carries_the_x11_id(monkeypatch):
    _wire_x11(monkeypatch, {
        "getactivewindow": "123456\n",
    })
    win = ws.foreground_window()
    assert win is not None
    assert win.handle == 123456


def test_wayland_frame_rect_is_none_without_probing(monkeypatch):
    monkeypatch.setattr(ws, "detect_platform", lambda: "linux")
    monkeypatch.setattr(ws, "is_wayland", lambda: True)

    def boom(cmd, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("no subprocess may run under Wayland")

    monkeypatch.setattr(ws.subprocess, "run", boom)
    assert ws.window_frame_rect(WindowInfo(title="App", handle=1)) is None


def test_linux_without_xdotool_degrades_to_none(monkeypatch):
    monkeypatch.setattr(ws, "detect_platform", lambda: "linux")
    monkeypatch.setattr(ws, "is_wayland", lambda: False)
    monkeypatch.setattr(ws, "display_present", lambda: True)
    monkeypatch.setattr(ws.shutil, "which", lambda name: None)
    assert ws.window_frame_rect(WindowInfo(title="App", handle=1)) is None


# ---------------------------------------------------------------------------
# Windows dispatch stays intact
# ---------------------------------------------------------------------------

def test_windows_frame_rect_without_handle_is_none(monkeypatch):
    monkeypatch.setattr(ws, "detect_platform", lambda: "win32")
    assert ws.window_frame_rect(WindowInfo(title="App")) is None


# ---------------------------------------------------------------------------
# Native per-window capture seam (jarvis.platform.window_capture)
# ---------------------------------------------------------------------------

def test_grab_window_returns_none_on_windows(monkeypatch):
    # Windows' native path IS the DPI-pinned rect grab (GDI) — no separate
    # per-window bitmap; callers keep the rect grabber.
    from jarvis.platform import window_capture as wc

    monkeypatch.setattr(wc, "detect_platform", lambda: "win32")
    assert wc.grab_window(1234, {"left": 0, "top": 0,
                                 "width": 100, "height": 100}) is None


def test_grab_window_macos_degrades_without_frameworks(monkeypatch):
    from jarvis.platform import window_capture as wc

    monkeypatch.setattr(wc, "detect_platform", lambda: "darwin")
    monkeypatch.setitem(sys.modules, "ScreenCaptureKit", None)
    monkeypatch.setitem(sys.modules, "Quartz", None)
    assert wc.grab_window(7, {"left": 0, "top": 0,
                              "width": 100, "height": 100}) is None


def test_grab_window_never_raises(monkeypatch):
    from jarvis.platform import window_capture as wc

    def boom():
        raise RuntimeError("platform probe exploded")

    monkeypatch.setattr(wc, "detect_platform", boom)
    assert wc.grab_window(7, {"left": 0, "top": 0,
                              "width": 100, "height": 100}) is None
