"""EdgeGlowWindow Smoke-Test — instantiiert headless, ohne ``show()``."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtWidgets")


def test_edge_glow_window_instantiates_headless(qapp) -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication

    from overlay.window_glow import EdgeGlowWindow

    primary = QGuiApplication.primaryScreen()
    assert primary is not None, "offscreen platform muss einen Screen liefern"

    win = EdgeGlowWindow(primary, hide_from_capture=False)
    try:
        # Sanity: Flags sind gesetzt — KEIN show() (Plan-Vorgabe).
        flags = win.windowFlags()
        assert flags & Qt.WindowType.FramelessWindowHint
        assert flags & Qt.WindowType.WindowStaysOnTopHint
        assert flags & Qt.WindowType.WindowTransparentForInput
        assert flags & Qt.WindowType.Tool
        assert flags & Qt.WindowType.NoDropShadowWindowHint

        assert win.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        assert win.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        assert win.testAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        assert win.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        # Geometrie matcht den Screen.
        assert win.geometry() == primary.geometry()
    finally:
        win.deleteLater()
