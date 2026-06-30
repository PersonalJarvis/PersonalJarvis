"""Absolute virtual-desktop click coordinates (audit 🔴 #7).

Clicks dispatched via MOUSEEVENTF_ABSOLUTE | VIRTUALDESK land on their exact
target on EVERY monitor — including one positioned LEFT of primary (negative
virtual-desktop X), where the old SetCursorPos-then-relative-click path lands in
the void. _normalize_virtualdesk folds the (possibly negative) virtual origin
into a 0..65535 coordinate; these guard that math.
"""
from __future__ import annotations

from jarvis.plugins.tool.click import _normalize_virtualdesk

# The reported two-monitor rig: left monitor at X<0 (-2560..0), main 4K at 0..3840.
_VX, _VY, _VW, _VH = -2560, 0, 6400, 2160


def test_left_monitor_negative_x_maps_to_valid_positive_coord():
    nx, ny = _normalize_virtualdesk(-1280, 720, _VX, _VY, _VW, _VH)
    assert 0 <= nx <= 65535 and 0 <= ny <= 65535
    assert 13000 < nx < 13200   # ~20% across the virtual desktop = left-monitor centre
    assert 21700 < ny < 22000


def test_main_monitor_maps_to_right_portion():
    nx, _ny = _normalize_virtualdesk(1920, 1080, _VX, _VY, _VW, _VH)
    assert 45700 < nx < 46050   # ~70% across = main-monitor centre


def test_left_is_left_of_main():
    nx_left, _ = _normalize_virtualdesk(-1280, 720, _VX, _VY, _VW, _VH)
    nx_main, _ = _normalize_virtualdesk(1920, 1080, _VX, _VY, _VW, _VH)
    assert nx_left < nx_main


def test_origin_corner_is_zero():
    assert _normalize_virtualdesk(_VX, _VY, _VX, _VY, _VW, _VH) == (0, 0)


def test_clamps_out_of_bounds():
    assert _normalize_virtualdesk(-99999, -99999, _VX, _VY, _VW, _VH) == (0, 0)
    assert _normalize_virtualdesk(99999, 99999, _VX, _VY, _VW, _VH) == (65535, 65535)


def test_single_monitor_at_origin_maps_linearly():
    # Primary-only 1920x1080: centre -> ~32767.
    nx, ny = _normalize_virtualdesk(960, 540, 0, 0, 1920, 1080)
    assert 32700 < nx < 32830 and 32700 < ny < 32830
