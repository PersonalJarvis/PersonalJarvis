"""Click coords map against the EXACT captured monitor (mixed-DPI fix, 2026-06-28).

On a 150%-primary + 100%-secondary desktop the screenshot (mss) and a separate
GetForegroundWindow/MonitorFromWindow click-geometry probe can pick different
monitors at a boundary, so clicks land on the wrong screen. The ScreenshotSource
now threads the captured monitor on the Observation, and ``_resolve_monitor_geom``
uses it — falling back to the win32 probe only when it is absent.
"""
from __future__ import annotations

from types import SimpleNamespace

from jarvis.harness import screenshot_only_loop as sol


def test_uses_observation_geometry_when_present(monkeypatch):
    monkeypatch.setattr(sol, "_capture_monitor_geometry", lambda: (0, 0, 1920, 1080))
    obs = SimpleNamespace(monitor_geom=(-2560, 0, 2560, 1440))  # the secondary
    assert sol._resolve_monitor_geom(obs) == (-2560, 0, 2560, 1440)


def test_falls_back_to_win32_probe_when_geom_unknown(monkeypatch):
    monkeypatch.setattr(sol, "_capture_monitor_geometry", lambda: (0, 0, 3840, 2160))
    obs = SimpleNamespace(monitor_geom=(0, 0, 0, 0))
    assert sol._resolve_monitor_geom(obs) == (0, 0, 3840, 2160)


def test_falls_back_when_observation_is_none(monkeypatch):
    monkeypatch.setattr(sol, "_capture_monitor_geometry", lambda: (7, 7, 800, 600))
    assert sol._resolve_monitor_geom(None) == (7, 7, 800, 600)


def test_falls_back_when_width_is_zero(monkeypatch):
    monkeypatch.setattr(sol, "_capture_monitor_geometry", lambda: (1, 2, 100, 200))
    obs = SimpleNamespace(monitor_geom=(-2560, 0, 0, 1440))  # bogus width
    assert sol._resolve_monitor_geom(obs) == (1, 2, 100, 200)


def test_secondary_geometry_resolves_click_to_secondary_pixel(monkeypatch):
    # End-to-end intent: a click at norm (500,500) on the SECONDARY (origin
    # -2560, 2560x1440) must resolve to that screen's centre pixel, not the
    # primary's. Proves the geometry the loop now feeds _resolve_click_pixel.
    monkeypatch.setattr(sol, "_capture_monitor_geometry", lambda: (0, 0, 3840, 2160))
    geom = sol._resolve_monitor_geom(SimpleNamespace(monitor_geom=(-2560, 0, 2560, 1440)))
    px, py = sol._resolve_click_pixel({"x": 500, "y": 500}, geom)
    assert px == -1280   # -2560 + 500/1000*2560
    assert py == 720     # 0 + 500/1000*1440
