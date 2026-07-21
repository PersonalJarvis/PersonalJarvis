"""Window-scoped capture target tests (no real display needed).

Guards the fix for the 2026-07-02 multi-monitor incident: a restored Chrome
floating on a 4K secondary monitor turned stamp-sized in the model's frame,
every grounding miss clicked the wallpaper next to it and stole the window's
focus. ``select_capture_target`` crops the capture to the foreground window
(industry framing: OpenAI CUA fixed viewport / Anthropic single small display
/ Microsoft UFO per-application screenshots), which also structurally clamps
every mapped click into the window rect.
"""
from __future__ import annotations

import pytest

from jarvis.cu import capture as capture_mod
from jarvis.cu.capture import select_capture_target
from jarvis.cu.geometry import MonitorInfo
from jarvis.platform import window_state as ws
from jarvis.platform.window_state import WindowInfo

MONITOR = MonitorInfo(left=-3840, top=0, width=3840, height=2160, name="left-4k")


@pytest.fixture()
def fake_monitor(monkeypatch: pytest.MonkeyPatch) -> MonitorInfo:
    monkeypatch.setattr(
        capture_mod, "select_monitor", lambda policy, main_monitor: MONITOR,
    )
    return MONITOR


def _wire_window(
    monkeypatch: pytest.MonkeyPatch,
    *,
    win: WindowInfo | None,
    rect: tuple[int, int, int, int] | None,
    is_shell: bool = False,
    menu_open: bool = False,
) -> None:
    monkeypatch.setattr(ws, "foreground_window", lambda: win)
    monkeypatch.setattr(ws, "is_shell_window", lambda w: is_shell)
    monkeypatch.setattr(ws, "window_frame_rect", lambda w: rect)
    monkeypatch.setattr(ws, "open_menu_surface_present", lambda: menu_open)


def test_monitor_scope_keeps_previous_behaviour(fake_monitor, monkeypatch):
    _wire_window(monkeypatch, win=WindowInfo(title="App", handle=7),
                 rect=(-2000, 100, 800, 600))
    got = select_capture_target("foreground", scope="monitor")
    assert got == fake_monitor


def test_policy_all_never_crops(fake_monitor, monkeypatch):
    _wire_window(monkeypatch, win=WindowInfo(title="App", handle=7),
                 rect=(-2000, 100, 800, 600))
    got = select_capture_target("all", scope="window")
    assert got == fake_monitor


def test_window_scope_crops_to_foreground_window(fake_monitor, monkeypatch):
    _wire_window(monkeypatch, win=WindowInfo(title="Google Chrome", handle=7),
                 rect=(-2000, 100, 1890, 1040))
    got = select_capture_target("foreground", scope="window")
    assert (got.left, got.top, got.width, got.height) == (-2000, 100, 1890, 1040)
    assert got.name.startswith("window:")


def test_shell_window_falls_back_to_monitor(fake_monitor, monkeypatch):
    _wire_window(monkeypatch, win=WindowInfo(title="Program Manager", handle=7),
                 rect=(-3840, 0, 3840, 2160), is_shell=True)
    assert select_capture_target("foreground", scope="window") == fake_monitor


def test_no_foreground_window_falls_back(fake_monitor, monkeypatch):
    _wire_window(monkeypatch, win=None, rect=None)
    assert select_capture_target("foreground", scope="window") == fake_monitor


def test_unreadable_rect_falls_back(fake_monitor, monkeypatch):
    # macOS/Linux today: no per-window rect -> keep the monitor framing.
    _wire_window(monkeypatch, win=WindowInfo(title="App"), rect=None)
    assert select_capture_target("foreground", scope="window") == fake_monitor


def test_open_menu_surface_falls_back_to_monitor(fake_monitor, monkeypatch):
    # A context menu is its own top-level hwnd: a native per-window grab can
    # NEVER contain it, so the model could not see (or click) the menu it
    # just opened. While one is open, perception must widen to the monitor
    # (Windows right-click dead-end, 2026-07-21).
    _wire_window(monkeypatch, win=WindowInfo(title="Google Chrome", handle=7),
                 rect=(-2000, 100, 1890, 1040), menu_open=True)
    assert select_capture_target("foreground", scope="window") == fake_monitor


def test_tiny_window_falls_back(fake_monitor, monkeypatch):
    _wire_window(monkeypatch, win=WindowInfo(title="Tooltip", handle=7),
                 rect=(-1000, 500, 120, 80))
    assert select_capture_target("foreground", scope="window") == fake_monitor


def test_window_overhanging_monitor_edge_is_clamped(fake_monitor, monkeypatch):
    # Window straddles the mixed-DPI boundary at x=0: the grab must never
    # cross coordinate spaces, so it is clamped to the selected monitor.
    _wire_window(monkeypatch, win=WindowInfo(title="App", handle=7),
                 rect=(-800, -50, 1600, 1200))
    got = select_capture_target("foreground", scope="window")
    assert (got.left, got.top) == (-800, 0)
    assert got.left + got.width <= MONITOR.left + MONITOR.width
    assert (got.width, got.height) == (800, 1150)


def test_window_fully_outside_monitor_falls_back(fake_monitor, monkeypatch):
    _wire_window(monkeypatch, win=WindowInfo(title="App", handle=7),
                 rect=(100, 100, 800, 600))  # on the primary, not the left 4k
    assert select_capture_target("foreground", scope="window") == fake_monitor


# ---------------------------------------------------------------------------
# normalize_foreground_window decision logic
# ---------------------------------------------------------------------------

def _wire_normalize(
    monkeypatch: pytest.MonkeyPatch,
    *,
    win: WindowInfo | None,
    is_shell: bool = False,
    maximized: bool | None = False,
) -> list[WindowInfo]:
    calls: list[WindowInfo] = []
    monkeypatch.setattr(ws, "foreground_window", lambda: win)
    monkeypatch.setattr(ws, "is_shell_window", lambda w: is_shell)
    monkeypatch.setattr(ws, "window_is_maximized", lambda w: maximized)
    monkeypatch.setattr(
        ws, "maximize_window",
        lambda w: (calls.append(w) or (True, "maximized")),
    )
    return calls


def test_normalize_maximizes_a_restored_window(monkeypatch):
    win = WindowInfo(title="Google Chrome", handle=7)
    calls = _wire_normalize(monkeypatch, win=win, maximized=False)
    ok, _ = ws.normalize_foreground_window()
    assert ok is True
    assert calls == [win]


def test_normalize_leaves_maximized_window_alone(monkeypatch):
    calls = _wire_normalize(
        monkeypatch, win=WindowInfo(title="App", handle=7), maximized=True,
    )
    ok, _ = ws.normalize_foreground_window()
    assert ok is False
    assert calls == []


def test_normalize_leaves_unknown_state_alone(monkeypatch):
    # macOS/Linux today: maximized state unreadable (None) -> do not touch.
    calls = _wire_normalize(
        monkeypatch, win=WindowInfo(title="App"), maximized=None,
    )
    ok, _ = ws.normalize_foreground_window()
    assert ok is False
    assert calls == []


def test_normalize_never_touches_the_shell(monkeypatch):
    calls = _wire_normalize(
        monkeypatch,
        win=WindowInfo(title="Program Manager", handle=7),
        is_shell=True,
        maximized=False,
    )
    ok, _ = ws.normalize_foreground_window()
    assert ok is False
    assert calls == []


def test_normalize_without_foreground_window(monkeypatch):
    calls = _wire_normalize(monkeypatch, win=None)
    ok, _ = ws.normalize_foreground_window()
    assert ok is False
    assert calls == []
