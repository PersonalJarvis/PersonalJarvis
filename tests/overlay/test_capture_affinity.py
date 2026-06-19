"""Capture-Affinity-Reapply auf show + screenChanged. Plan §18.1."""

from __future__ import annotations

from unittest import mock

import pytest

pytest.importorskip("PySide6.QtWidgets")


def test_reapply_capture_affinity_calls_set_window_display_affinity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Plan §18.1: reapply_capture_affinity() muss
    SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE) feuern."""
    from overlay import transparency

    fake_user32 = mock.MagicMock()
    fake_user32.SetWindowDisplayAffinity.return_value = 1

    monkeypatch.setattr(transparency, "_is_windows", lambda: True)
    monkeypatch.setattr(transparency, "get_user32", lambda: fake_user32)

    result = transparency.reapply_capture_affinity(0xDEAD)
    assert result is True
    fake_user32.SetWindowDisplayAffinity.assert_called_once()
    args, _ = fake_user32.SetWindowDisplayAffinity.call_args
    # Erstes arg ist HWND wrapper, zweites WDA_EXCLUDEFROMCAPTURE wrapper.
    assert int(args[1].value) == transparency.WDA_EXCLUDEFROMCAPTURE


def test_reapply_capture_affinity_noop_on_non_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from overlay import transparency

    monkeypatch.setattr(transparency, "_is_windows", lambda: False)
    assert transparency.reapply_capture_affinity(0xDEAD) is False


# -------------------------------------------------------------------------
# Window-Hooks: showEvent + screenChanged-Reapply
# -------------------------------------------------------------------------


def test_edge_glow_window_calls_exclude_from_capture_on_show(
    qapp, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Plan §18.1: showEvent muss exclude_from_capture rufen."""
    from PySide6.QtGui import QGuiApplication

    from overlay import window_glow

    captured = []
    monkeypatch.setattr(
        window_glow, "exclude_from_capture", lambda hwnd: captured.append(hwnd)
    )

    primary = QGuiApplication.primaryScreen()
    win = window_glow.EdgeGlowWindow(primary, hide_from_capture=True)
    try:
        win.show()
        qapp.processEvents()
        assert len(captured) >= 1
    finally:
        win.close()
        win.deleteLater()


def test_edge_glow_window_skips_affinity_when_disabled(
    qapp, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PySide6.QtGui import QGuiApplication

    from overlay import window_glow

    called = []
    monkeypatch.setattr(
        window_glow, "exclude_from_capture", lambda hwnd: called.append(hwnd)
    )

    primary = QGuiApplication.primaryScreen()
    win = window_glow.EdgeGlowWindow(primary, hide_from_capture=False)
    try:
        win.show()
        qapp.processEvents()
        assert called == []
    finally:
        win.close()
        win.deleteLater()


def test_edge_glow_screen_change_reapplies_affinity(
    qapp, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Plan §18.1: screenChanged muss reapply_capture_affinity rufen."""
    from PySide6.QtGui import QGuiApplication

    from overlay import window_glow

    reapplied = []
    monkeypatch.setattr(
        window_glow,
        "reapply_capture_affinity",
        lambda hwnd: reapplied.append(hwnd),
    )

    primary = QGuiApplication.primaryScreen()
    win = window_glow.EdgeGlowWindow(primary, hide_from_capture=True)
    try:
        win.show()
        qapp.processEvents()
        # Direkter Aufruf des Handlers — wir koennen den Qt-screenChanged-
        # Signal nicht zuverlaessig im offscreen-Plattform-Test triggern.
        win._on_screen_changed(primary)
        assert len(reapplied) == 1
    finally:
        win.close()
        win.deleteLater()


def test_mascot_screen_change_reapplies_affinity(
    qapp, monkeypatch: pytest.MonkeyPatch
) -> None:
    from overlay import window_mascot

    reapplied = []
    monkeypatch.setattr(
        window_mascot,
        "reapply_capture_affinity",
        lambda hwnd: reapplied.append(hwnd),
    )

    win = window_mascot.MascotWindow(
        initial_x=100,
        initial_y=100,
        monitor_name="X",
        hide_from_capture=True,
    )
    try:
        win.show()
        qapp.processEvents()
        win._on_screen_changed(None)
        assert len(reapplied) == 1
    finally:
        win.close()
        win.deleteLater()


def test_set_view_visible_toggles_webview(qapp) -> None:
    """Plan §17.3 — Hide-on-Idle 5min muss set_view_visible(False) auf
    den WebView durchreichen."""
    from PySide6.QtGui import QGuiApplication

    from overlay import window_glow

    primary = QGuiApplication.primaryScreen()
    win = window_glow.EdgeGlowWindow(primary, hide_from_capture=False)
    try:
        win.show()
        qapp.processEvents()
        assert win._view.isVisible() is True
        win.set_view_visible(False)
        assert win._view.isVisible() is False
        win.set_view_visible(True)
        assert win._view.isVisible() is True
    finally:
        win.close()
        win.deleteLater()
