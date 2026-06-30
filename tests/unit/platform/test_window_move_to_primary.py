"""Move the target window onto the primary monitor (audit G8b).

The pure geometry (which monitor a window is on) is unit-tested here; the per-OS
move is best-effort (Windows full + live-proven elsewhere, Wayland an honest
refusal, never a silent wrong-monitor act). The dispatch's refusal paths are
pinned by monkeypatching the platform probes.
"""
from __future__ import annotations

from jarvis.platform import window_state as ws
from jarvis.platform.window_state import WindowInfo

# --- pure: window_is_on_monitor ----------------------------------------------

_MAIN = {"left": 0, "top": 0, "width": 3840, "height": 2160}
_LEFT = {"left": -2560, "top": 0, "width": 2560, "height": 1440}


def test_window_centered_on_main_is_on_main():
    # A window at (100,100) 800x600 -> center (500,400) is on MAIN, not LEFT.
    assert ws.window_is_on_monitor((100, 100, 800, 600), _MAIN) is True
    assert ws.window_is_on_monitor((100, 100, 800, 600), _LEFT) is False


def test_window_on_left_monitor_negative_x():
    # A window on the LEFT screen: left=-2000, center ~ -1600 -> on LEFT.
    rect = (-2000, 200, 800, 600)
    assert ws.window_is_on_monitor(rect, _LEFT) is True
    assert ws.window_is_on_monitor(rect, _MAIN) is False


def test_straddling_window_counts_where_its_center_is():
    # Center decides: a window mostly on MAIN (center x>0) is "on MAIN".
    rect = (-200, 100, 800, 600)  # center x = 200 -> MAIN
    assert ws.window_is_on_monitor(rect, _MAIN) is True


# --- dispatch refusals (no silent wrong-monitor act) -------------------------


def test_move_refuses_on_wayland(monkeypatch):
    monkeypatch.setattr(ws, "detect_platform", lambda: "linux")
    monkeypatch.setattr(ws, "is_wayland", lambda: True)
    ok, msg = ws.move_window_to_primary(WindowInfo(title="App"), _MAIN)
    assert ok is False
    assert "wayland" in msg.lower()


def test_move_refuses_on_headless_linux(monkeypatch):
    monkeypatch.setattr(ws, "detect_platform", lambda: "linux")
    monkeypatch.setattr(ws, "is_wayland", lambda: False)
    monkeypatch.setattr(ws, "display_present", lambda: False)
    ok, msg = ws.move_window_to_primary(WindowInfo(title="App"), _MAIN)
    assert ok is False
    assert "headless" in msg.lower() or "display" in msg.lower()


def test_move_never_raises_on_unknown_platform(monkeypatch):
    monkeypatch.setattr(ws, "detect_platform", lambda: "plan9")
    ok, msg = ws.move_window_to_primary(WindowInfo(title="App"), _MAIN)
    assert ok is False
    assert isinstance(msg, str) and msg


def test_windows_move_without_handle_fails_cleanly(monkeypatch):
    monkeypatch.setattr(ws, "detect_platform", lambda: "win32")
    ok, msg = ws.move_window_to_primary(WindowInfo(title="App", handle=None), _MAIN)
    assert ok is False
    assert "handle" in msg.lower()
