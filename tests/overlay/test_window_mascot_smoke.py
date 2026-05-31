"""MascotWindow smoke — instantiiert headless, ohne show()."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtWidgets")


def test_mascot_window_instantiates_headless(qapp, tmp_path) -> None:
    from PySide6.QtCore import Qt

    from overlay.state import StateMachine
    from overlay.window_mascot import MascotWindow

    machine = StateMachine()

    saved = []

    def _save(pos) -> None:
        saved.append(pos)

    win = MascotWindow(
        initial_x=100,
        initial_y=200,
        monitor_name="\\\\.\\DISPLAY1",
        size_px=160,
        hide_from_capture=False,
        state_machine=machine,
        on_position_saved=_save,
    )
    try:
        flags = win.windowFlags()
        # Plan §12.2 — exakt diese Flag-Kombination, OHNE
        # WindowTransparentForInput.
        assert flags & Qt.WindowType.FramelessWindowHint
        assert flags & Qt.WindowType.WindowStaysOnTopHint
        assert flags & Qt.WindowType.Tool
        assert flags & Qt.WindowType.NoDropShadowWindowHint
        assert not (flags & Qt.WindowType.WindowTransparentForInput)

        assert win.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        assert win.testAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        assert win.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        # WICHTIG: Mascot ist NICHT TransparentForMouseEvents.
        assert not win.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        # Geometry + Mask.
        assert win.size().width() == 160
        assert win.size().height() == 160
        # Mask ist gesetzt (kann nicht direkt verglichen werden,
        # nur dass mask().isEmpty() False ist).
        assert not win.mask().isEmpty()

        # Position.
        assert win.x() == 100
        assert win.y() == 200
    finally:
        win.deleteLater()


def test_mascot_window_skips_state_bridge_without_machine(qapp) -> None:
    from overlay.window_mascot import MascotWindow

    win = MascotWindow(
        initial_x=0,
        initial_y=0,
        monitor_name="X",
        state_machine=None,
        hide_from_capture=False,
    )
    try:
        # Bridge sollte None sein wenn keine Machine.
        assert win._bridge is None  # noqa: SLF001
        assert win._channel is None  # noqa: SLF001
    finally:
        win.deleteLater()
