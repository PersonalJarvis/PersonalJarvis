"""Monitor-Enumeration + Hotplug — mit gemockter QGuiApplication."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytest.importorskip("PySide6.QtGui")


def _make_screen(name: str, x: int, y: int, w: int, h: int, dpr: float = 1.0) -> MagicMock:
    screen = MagicMock()
    screen.name.return_value = name
    geo = MagicMock()
    geo.x.return_value = x
    geo.y.return_value = y
    geo.width.return_value = w
    geo.height.return_value = h
    screen.geometry.return_value = geo
    screen.devicePixelRatio.return_value = dpr
    return screen


def test_enumerate_monitors_with_qapp(qapp) -> None:
    """Echte Qt-Headless-Enumeration: liefert mindestens den Offscreen-Default."""
    from overlay.monitors import enumerate_monitors

    monitors = enumerate_monitors()
    # offscreen-Plattform liefert mindestens einen virtuellen Screen,
    # dessen ``name`` aber leer sein darf.
    assert isinstance(monitors, list)
    assert len(monitors) >= 1
    m = monitors[0]
    assert isinstance(m.name, str)
    assert len(m.geometry) == 4
    assert m.device_pixel_ratio > 0


def test_enumerate_monitors_without_qapp(monkeypatch) -> None:
    """Ohne QGuiApplication.instance() → leere Liste."""
    from overlay import monitors as mod

    fake_qgui = MagicMock()
    fake_qgui.instance.return_value = None
    monkeypatch.setattr("PySide6.QtGui.QGuiApplication", fake_qgui, raising=False)
    # Re-Import verschonen wir uns; enumerate_monitors prueft instance() selbst.
    assert mod.enumerate_monitors() == []


def test_monitor_manager_attach_requires_qapp(monkeypatch) -> None:
    from overlay.monitors import MonitorManager

    fake_qgui = MagicMock()
    fake_qgui.instance.return_value = None
    monkeypatch.setattr("PySide6.QtGui.QGuiApplication", fake_qgui, raising=False)

    mgr = MonitorManager()
    with pytest.raises(RuntimeError):
        mgr.attach()


def test_monitor_manager_connects_callbacks(qapp) -> None:
    """Mit echter QApplication: attach + detach binden/loesen Signals."""
    from overlay.monitors import MonitorManager

    added_calls: list = []
    removed_calls: list = []

    mgr = MonitorManager(
        on_screen_added=added_calls.append,
        on_screen_removed=removed_calls.append,
    )
    mgr.attach()
    # Idempotent — a second attach() must not connect twice.
    mgr.attach()
    mgr.detach()
    # Detach idempotent.
    mgr.detach()


def test_monitor_info_dataclass() -> None:
    from overlay.monitors import MonitorInfo

    m = MonitorInfo(name="\\\\.\\DISPLAY1", geometry=(0, 0, 1920, 1080), device_pixel_ratio=1.0, is_primary=True)
    assert m.is_primary is True
    assert m.geometry == (0, 0, 1920, 1080)
