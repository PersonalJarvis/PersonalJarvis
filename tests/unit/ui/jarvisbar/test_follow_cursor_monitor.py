"""Follow-the-active-monitor behaviour for the Jarvis Bar surfaces.

Covers the two surfaces' migration math (Tk on Windows/Linux, Qt on macOS) with
bare objects — no real window — plus the cross-platform monitor probe used by
the Tk path. The pure relative-placement helpers themselves are in
``test_interaction.py``; here we verify the surfaces call them correctly and
only migrate when the cursor's monitor actually changes.
"""
from __future__ import annotations

from jarvis.ui.jarvisbar import interaction, renderer
from jarvis.ui.jarvisbar.overlay import JarvisBarOverlay


# --------------------------------------------------------------------------- #
# Tk surface (Windows / Linux)                                                #
# --------------------------------------------------------------------------- #
def _bare_tk_bar(follow: bool = True) -> JarvisBarOverlay:
    bar = JarvisBarOverlay.__new__(JarvisBarOverlay)
    bar._root = None
    bar._drag = None
    bar._follow_cursor = follow
    bar._startup_gated = False
    bar._x, bar._y = 0, 0
    bar._rel_pos = None
    bar._cur_work = None
    return bar


def test_tk_migrates_to_the_cursor_monitor_keeping_the_relative_spot(monkeypatch):
    bar = _bare_tk_bar()
    primary = (0, 0, 1920, 1080)
    secondary = (1920, 0, 2560, 1440)  # a bigger monitor to the right
    # The bar sits bottom-centre on the primary monitor.
    bx = (1920 - renderer.WIN_W) // 2
    by = 1080 - renderer.WIN_H
    bar._x, bar._y = bx, by
    bar._cur_work = primary
    bar._rel_pos = interaction.relative_within(
        bx, by, work=primary, bar_w=renderer.WIN_W, bar_h=renderer.WIN_H
    )
    # Cursor is now on the secondary monitor.
    monkeypatch.setattr(bar, "_cursor_global", lambda: (2020, 200))
    monkeypatch.setattr(bar, "_work_area_for_point", lambda x, y: secondary)

    assert bar._project_onto_cursor_monitor() is True
    # Reprojected to bottom-centre of the SECONDARY monitor (same relative spot),
    # even though it is a different size — the whole point of the feature.
    assert bar._cur_work == secondary
    assert bar._x == 1920 + (2560 - renderer.WIN_W) // 2
    assert bar._y == 1440 - renderer.WIN_H


def test_tk_does_not_migrate_while_cursor_stays_on_the_same_monitor(monkeypatch):
    bar = _bare_tk_bar()
    primary = (0, 0, 1920, 1080)
    bar._x, bar._y = 810, 1000
    bar._cur_work = primary
    bar._rel_pos = (0.5, 1.0)
    monkeypatch.setattr(bar, "_cursor_global", lambda: (500, 500))
    monkeypatch.setattr(bar, "_work_area_for_point", lambda x, y: primary)
    assert bar._project_onto_cursor_monitor() is False
    assert (bar._x, bar._y) == (810, 1000)  # untouched


def test_tk_follow_disabled_never_migrates(monkeypatch):
    bar = _bare_tk_bar(follow=False)
    bar._x, bar._y = 810, 1000
    bar._cur_work = (0, 0, 1920, 1080)
    bar._rel_pos = (0.5, 1.0)
    # Even with the cursor on another monitor, a disabled bar stays put.
    monkeypatch.setattr(bar, "_cursor_global", lambda: (2020, 500))
    monkeypatch.setattr(bar, "_work_area_for_point", lambda x, y: (1920, 0, 2560, 1440))
    assert bar._project_onto_cursor_monitor() is False
    assert (bar._x, bar._y) == (810, 1000)


def test_tk_no_migration_during_a_drag(monkeypatch):
    bar = _bare_tk_bar()
    bar._drag = {"moved": True}  # a drag in progress
    bar._cur_work = (0, 0, 1920, 1080)
    bar._rel_pos = (0.5, 1.0)
    monkeypatch.setattr(bar, "_cursor_global", lambda: (2020, 500))
    monkeypatch.setattr(bar, "_work_area_for_point", lambda x, y: (1920, 0, 2560, 1440))
    assert bar._project_onto_cursor_monitor() is False


# --------------------------------------------------------------------------- #
# Qt surface (macOS)                                                          #
# --------------------------------------------------------------------------- #
class _FakeRect:
    """A minimal QRect-compatible double for _geometry_bounds()."""

    def __init__(self, x: int, y: int, w: int, h: int) -> None:
        self._v = (x, y, w, h)

    def x(self) -> int:
        return self._v[0]

    def y(self) -> int:
        return self._v[1]

    def width(self) -> int:
        return self._v[2]

    def height(self) -> int:
        return self._v[3]


def test_qt_migrates_to_the_cursor_monitor(monkeypatch):
    from jarvis.ui.jarvisbar.qt_overlay import QtJarvisBarOverlay

    bar = QtJarvisBarOverlay.__new__(QtJarvisBarOverlay)
    bar._window = object()
    bar._follow_cursor = True
    bar._drag = None
    primary = (0, 0, 1920, 1080)
    secondary = (1920, 0, 2560, 1440)
    px = (1920 - renderer.WIN_W) // 2
    py = 1080 - renderer.WIN_H
    bar._preferred_position = (px, py)
    bar._x, bar._y = px, py
    bar._cur_work = primary
    bar._rel_pos = interaction.relative_within(
        px, py, work=primary, bar_w=renderer.WIN_W, bar_h=renderer.WIN_H
    )
    monkeypatch.setattr(
        bar, "_cursor_screen_geometry_ui", lambda **k: _FakeRect(*secondary)
    )
    reconciled: dict[str, object] = {}

    def _reconcile() -> bool:
        reconciled["pref"] = bar._preferred_position
        return True

    monkeypatch.setattr(bar, "_reconcile_dynamic_position_ui", _reconcile)

    assert bar._project_onto_cursor_monitor_ui() is True
    assert bar._cur_work == secondary
    assert bar._preferred_position == (
        1920 + (2560 - renderer.WIN_W) // 2,
        1440 - renderer.WIN_H,
    )
    assert reconciled["pref"] == bar._preferred_position  # reconcile ran


def test_qt_no_migration_when_monitor_unchanged(monkeypatch):
    from jarvis.ui.jarvisbar.qt_overlay import QtJarvisBarOverlay

    bar = QtJarvisBarOverlay.__new__(QtJarvisBarOverlay)
    bar._window = object()
    bar._follow_cursor = True
    bar._drag = None
    primary = (0, 0, 1920, 1080)
    bar._preferred_position = (810, 1000)
    bar._x, bar._y = 810, 1000
    bar._cur_work = primary
    bar._rel_pos = (0.5, 1.0)
    monkeypatch.setattr(
        bar, "_cursor_screen_geometry_ui", lambda **k: _FakeRect(*primary)
    )
    assert bar._project_onto_cursor_monitor_ui() is False


# --------------------------------------------------------------------------- #
# Cross-platform monitor probe (Tk path)                                      #
# --------------------------------------------------------------------------- #
def test_work_area_at_returns_none_under_wayland(monkeypatch):
    import jarvis.platform.monitors as monitors
    import jarvis.platform.probes as probes

    monkeypatch.setattr(monitors.sys, "platform", "linux")
    monkeypatch.setattr(probes, "is_wayland", lambda: True)
    assert monitors.work_area_at(10, 10) is None


def test_x11_work_area_at_picks_the_monitor_under_the_point(monkeypatch):
    import jarvis.platform.monitors as monitors

    xrandr_out = (
        "Screen 0: minimum 320 x 200, current 5760 x 2160\n"
        "DP-1 connected primary 1920x1080+0+0 (normal) 520mm x 290mm\n"
        "HDMI-1 connected 3840x2160+1920+0 (normal) 600mm x 340mm\n"
        "DP-2 disconnected (normal left inverted right x axis y axis)\n"
    )

    class _Proc:
        returncode = 0
        stdout = xrandr_out

    monkeypatch.setattr(monitors.shutil, "which", lambda name: "/usr/bin/xrandr")
    monkeypatch.setattr(monitors.subprocess, "run", lambda *a, **k: _Proc())

    assert monitors._x11_work_area_at(2000, 100) == (1920, 0, 3840, 2160)
    assert monitors._x11_work_area_at(100, 100) == (0, 0, 1920, 1080)


def test_x11_work_area_at_none_when_xrandr_missing(monkeypatch):
    import jarvis.platform.monitors as monitors

    monkeypatch.setattr(monitors.shutil, "which", lambda name: None)
    assert monitors._x11_work_area_at(0, 0) is None
