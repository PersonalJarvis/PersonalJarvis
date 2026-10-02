"""PySide6 area picker for region appshots.

Runs ONLY inside ``python -m jarvis.appshot.picker``; the main process never
imports this module (AP-26). One dimmed, frameless, always-on-top window per
screen with a crosshair: drag a rectangle (live size readout in real pixels),
release to confirm, Esc or right-click to cancel. The selection is reported as
fractions of its screen, so mixed-DPI layouts map back to capture pixels
exactly (see :mod:`jarvis.appshot.region`).
"""

from __future__ import annotations

import sys
import threading
from contextlib import suppress

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, Signal, Slot
from PySide6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QFontMetricsF,
    QGuiApplication,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import QApplication, QWidget

from jarvis.appshot import picker as wire
from jarvis.appshot.region import selection_fractions

# Jarvis gold, the same edge colour as the capture border and the shutter.
_GOLD = (255, 229, 0)
_SOFT_GOLD = (231, 196, 110)
_DIM_ALPHA = 105


def _screen_info(screen) -> dict[str, float]:
    g = screen.geometry()
    return {
        "x": float(g.x()),
        "y": float(g.y()),
        "w": float(g.width()),
        "h": float(g.height()),
        "dpr": float(screen.devicePixelRatio() or 1.0),
    }


def _emit(payload: dict) -> None:
    # A failed write means the parent is gone; exiting is all that is left.
    with suppress(Exception):
        sys.stdout.write(wire.encode(payload))
        sys.stdout.flush()


class _SelectWindow(QWidget):
    """One screen's dimmed selection canvas."""

    def __init__(self, screen, owner: Picker) -> None:
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        if sys.platform == "darwin":
            # Qt::Tool is an NSPanel on macOS; without this it stays invisible
            # while this never-activated process is in the background.
            always_show = getattr(Qt.WidgetAttribute, "WA_MacAlwaysShowToolWindow", None)
            if always_show is not None:
                self.setAttribute(always_show)
        self.setScreen(screen)
        self.setGeometry(screen.geometry())
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.screen_ref = screen
        self._owner = owner
        self._start: QPointF | None = None
        self._end: QPointF | None = None
        self._pointer: QPointF | None = None

    def keyPressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.key() == Qt.Key.Key_Escape:
            self._owner.finish(None, None)
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.RightButton:
            self._owner.finish(None, None)
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._start = event.position()
            self._end = event.position()
            self._owner.focus_on(self)
            self.update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        self._pointer = event.position()
        if self._start is not None:
            self._end = event.position()
        self._owner.focus_on(self)
        self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton or self._start is None:
            return
        end = event.position()
        frac = selection_fractions(
            self._start.x(), self._start.y(), end.x(), end.y(), self.width(), self.height()
        )
        self._start = None
        if frac is None:
            # A click without a drag: keep the overlay up for a real selection.
            self.update()
            return
        self._owner.finish(self, frac)

    def clear_pointer(self) -> None:
        if self._pointer is not None or self._start is not None:
            self._pointer = None
            self._start = None
            self.update()

    def _selection(self) -> QRectF | None:
        if self._start is None or self._end is None:
            return None
        return QRectF(self._start, self._end).normalized()

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        full = QRectF(0, 0, self.width(), self.height())
        sel = self._selection()
        dim = QPainterPath()
        dim.addRect(full)
        if sel is not None:
            hole = QPainterPath()
            hole.addRect(sel)
            dim = dim.subtracted(hole)
        painter.fillPath(dim, QColor(0, 0, 0, _DIM_ALPHA))
        if sel is not None:
            pen = QPen(QColor(*_GOLD, 235))
            pen.setWidthF(1.5)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(sel)
            self._paint_size(painter, sel)
        elif self._pointer is not None:
            pen = QPen(QColor(255, 255, 255, 120))
            pen.setWidthF(1.0)
            painter.setPen(pen)
            p = self._pointer
            painter.drawLine(QPointF(0, p.y()), QPointF(self.width(), p.y()))
            painter.drawLine(QPointF(p.x(), 0), QPointF(p.x(), self.height()))
        if self._owner.hint and self._owner.hint_window is self:
            self._paint_hint(painter, self._owner.hint)
        painter.end()

    def _paint_size(self, painter: QPainter, sel: QRectF) -> None:
        dpr = float(self.screen_ref.devicePixelRatio() or 1.0)
        text = f"{round(sel.width() * dpr)} × {round(sel.height() * dpr)}"
        font = QFont()
        font.setPointSizeF(9.5)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        metrics = QFontMetricsF(font)
        w = metrics.horizontalAdvance(text) + 16.0
        h = metrics.height() + 8.0
        x = sel.right() - w
        y = sel.bottom() + 6.0
        if y + h > self.height():
            y = sel.bottom() - h - 6.0
        x = max(4.0, min(x, self.width() - w - 4.0))
        box = QRectF(x, y, w, h)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(18, 18, 18, 210))
        painter.drawRoundedRect(box, h / 2.0, h / 2.0)
        painter.setPen(QColor(255, 244, 205, 240))
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, text)

    def _paint_hint(self, painter: QPainter, hint: str) -> None:
        font = QFont()
        font.setPointSizeF(10.5)
        font.setWeight(QFont.Weight.Medium)
        painter.setFont(font)
        metrics = QFontMetricsF(font)
        w = metrics.horizontalAdvance(hint) + 32.0
        h = metrics.height() + 14.0
        box = QRectF((self.width() - w) / 2.0, 24.0, w, h)
        painter.setPen(QPen(QColor(*_SOFT_GOLD, 200), 1.0))
        painter.setBrush(QColor(18, 18, 18, 190))
        painter.drawRoundedRect(box, h / 2.0, h / 2.0)
        painter.setPen(QColor(255, 240, 200, 240))
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, hint)


class Picker(QObject):
    """Owns the per-screen windows and reports exactly one result."""

    def __init__(self, app: QApplication, hint: str) -> None:
        super().__init__()
        self._app = app
        self.hint = hint
        self._windows: list[_SelectWindow] = []
        self.hint_window: _SelectWindow | None = None
        self._done = False

    def start(self) -> None:
        self._windows = [_SelectWindow(s, self) for s in QGuiApplication.screens()]
        cursor_screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        self.hint_window = next(
            (w for w in self._windows if w.screen_ref is cursor_screen),
            self._windows[0] if self._windows else None,
        )
        for win in self._windows:
            win.show()
        if self.hint_window is not None:
            self.hint_window.raise_()
            self.hint_window.activateWindow()
            self.hint_window.setFocus()
        self._app.processEvents()
        _emit({"event": wire.EVENT_READY})

    def focus_on(self, window: _SelectWindow) -> None:
        """The pointer moved onto ``window``: only that screen shows a guide."""
        for other in self._windows:
            if other is not window:
                other.clear_pointer()
        if self.hint_window is not window:
            self.hint_window = window
            for win in self._windows:
                win.update()

    def finish(self, window: _SelectWindow | None, frac) -> None:
        if self._done:
            return
        self._done = True
        info = _screen_info(window.screen_ref) if window is not None else None
        for win in self._windows:
            win.hide()
        # The dimmed overlay must be off the glass before the parent grabs.
        self._app.processEvents()
        if info is None or frac is None:
            _emit({"event": wire.EVENT_SELECTION, "cancelled": True})
        else:
            _emit({"event": wire.EVENT_SELECTION, "screen": info, "rect": list(frac)})
        self._app.quit()

    @Slot(str)
    def on_line(self, raw: str) -> None:
        payload = wire.decode(raw)
        if payload is not None and payload.get("cmd") == wire.CMD_CANCEL:
            self.finish(None, None)

    @Slot()
    def on_eof(self) -> None:
        self.finish(None, None)


class _StdinPump(QObject):
    """Reads stdin on a daemon thread; signals deliver to the Qt thread."""

    line = Signal(str)
    eof = Signal()

    def start(self) -> None:
        threading.Thread(target=self._run, name="appshot-picker-stdin", daemon=True).start()

    def _run(self) -> None:
        # A dying pipe simply means "parent gone" — treated as EOF.
        with suppress(Exception):
            for raw in sys.stdin:
                self.line.emit(raw)
        self.eof.emit()


def run(*, hint: str = "") -> int:
    """Picker main loop. Returns the process exit code."""
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    try:
        app = QApplication(sys.argv[:1] or ["appshot-picker"])
    except Exception as exc:  # noqa: BLE001 - no display / no platform plugin
        sys.stderr.write(f"appshot-picker: no usable display ({exc!r}).\n")
        return wire.EXIT_NO_GUI
    app.setQuitOnLastWindowClosed(False)
    picker = Picker(app, hint)
    pump = _StdinPump()
    pump.line.connect(picker.on_line, Qt.ConnectionType.QueuedConnection)
    pump.eof.connect(picker.on_eof, Qt.ConnectionType.QueuedConnection)
    pump.start()
    picker.start()
    return app.exec()


__all__ = ["Picker", "run"]
