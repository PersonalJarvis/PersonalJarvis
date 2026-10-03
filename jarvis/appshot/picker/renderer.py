"""PySide6 area picker for region appshots, as quiet as macOS's Cmd+Shift+4.

Runs ONLY inside ``python -m jarvis.appshot.picker``; the main process never
imports this module (AP-26). One frameless, always-on-top window per screen:

- **Frozen screen.** Each screen is grabbed once before the overlay appears
  and shown under a light dim, so nothing moves under the selection. Where the
  grab fails (no permission, odd platform) a translucent dim layer over the
  live desktop stands in.
- **Crosshair.** The pointer is a thin crosshair with a small ring in the
  middle; beside it, two small numbers — the position before the drag, the
  width and height while dragging. No lens, no labels in boxes, no banner.
- **Window snapping.** Hovering lifts the window under the pointer out of the
  dim (rectangles from the main process, top-most first); a click without a
  drag takes exactly that window.
- **Drag to select.** Like CleanShot X, the screen around the selection
  clears and the selected area itself turns a translucent grey, with one thin
  border.

Only the parts that change are repainted, so the numbers follow the pointer
without dragging a full 4K repaint behind them. Esc or a right-click cancels.
The result is reported as fractions of its screen, so mixed-DPI layouts map
back to capture pixels exactly (see :mod:`jarvis.appshot.region`).
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
    QPixmap,
)
from PySide6.QtWidgets import QApplication, QWidget

from jarvis.appshot import picker as wire
from jarvis.appshot.region import match_monitor, selection_fractions, snap_rects_on_screen

#: A light veil before the drag (the picker is armed). While dragging the
#: veil lifts and the selection itself takes a neutral grey tint instead, so
#: the chosen area reads as "marked" on light and dark content alike.
_DIM_IDLE = QColor(0, 0, 0, 55)
_SELECTION_TINT = QColor(128, 128, 132, 105)
#: The selection border: a white hairline with a faint dark edge outside it,
#: so it reads on light and dark content alike.
_BORDER = QColor(255, 255, 255, 235)
_BORDER_EDGE = QColor(0, 0, 0, 70)

#: The numbers beside the crosshair: dark ink with a white halo, like macOS.
_INK = QColor(20, 20, 22)
_HALO = QColor(255, 255, 255, 235)
_NUMBER_PT = 8.5
#: Offset of the numbers from the crosshair centre, and the screen-edge gap.
_NUMBER_OFFSET = QPointF(12.0, 10.0)
_EDGE_GAP = 4.0

#: Crosshair cursor geometry (logical px): arm length, ring radius.
_CROSS_ARM = 11
_CROSS_RING = 3.5

#: Windows smaller than this (logical px, either side) are not snap targets.
_MIN_SNAP_PX = 24


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


def _number_font() -> QFont:
    font = QFont()
    font.setPointSizeF(_NUMBER_PT)
    font.setWeight(QFont.Weight.DemiBold)
    font.setStyleHint(QFont.StyleHint.SansSerif)
    return font


def crosshair_cursor(dpr: float) -> QCursor:
    """The macOS-style crosshair: two thin arms with a ring at the centre.

    Drawn by the OS as the real cursor (no lag behind the mouse), at the
    screen's pixel density, black on a white halo so it reads everywhere.
    """
    arm = _CROSS_ARM
    side = arm * 2 + 3
    scale = max(1.0, float(dpr))
    pixmap = QPixmap(round(side * scale), round(side * scale))
    pixmap.setDevicePixelRatio(scale)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    c = side / 2.0
    gap = _CROSS_RING + 1.5
    segments = (
        (QPointF(c - arm, c), QPointF(c - gap, c)),
        (QPointF(c + gap, c), QPointF(c + arm, c)),
        (QPointF(c, c - arm), QPointF(c, c - gap)),
        (QPointF(c, c + gap), QPointF(c, c + arm)),
    )
    for colour, width in ((_HALO, 3.0), (_INK, 1.1)):
        pen = QPen(colour, width)
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for a, b in segments:
            painter.drawLine(a, b)
        painter.drawEllipse(QPointF(c, c), _CROSS_RING, _CROSS_RING)
    painter.end()
    hot = round(c)
    return QCursor(pixmap, hot, hot)


class _SelectWindow(QWidget):
    """One screen's selection canvas."""

    def __init__(self, screen, frozen: QPixmap | None, owner: Picker) -> None:
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.NoDropShadowWindowHint
        )
        if frozen is None:
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        if sys.platform == "darwin":
            # Qt::Tool is an NSPanel on macOS; without this it stays invisible
            # while this never-activated process is in the background.
            always_show = getattr(Qt.WidgetAttribute, "WA_MacAlwaysShowToolWindow", None)
            if always_show is not None:
                self.setAttribute(always_show)
        self.setScreen(screen)
        self.setGeometry(screen.geometry())
        self.setCursor(crosshair_cursor(screen.devicePixelRatio() or 1.0))
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.screen_ref = screen
        self._frozen = frozen
        self._owner = owner
        self._start: QPointF | None = None
        self._end: QPointF | None = None
        self._pointer: QPointF | None = None
        #: Snap targets in this window's logical coordinates, top-most first.
        self._snaps: list[QRectF] = []
        self._monitor: dict | None = None
        self._font = _number_font()
        self._metrics = QFontMetricsF(self._font)
        #: What was painted last, so the next move repaints only what changed.
        self._painted_numbers: QRectF | None = None
        self._painted_hole: QRectF | None = None

    # -- data from the main process ------------------------------------------
    def set_layout(self, monitors: list[dict], windows: list[list[int]]) -> None:
        """Map capture-space window rects onto this screen's logical pixels."""
        info = _screen_info(self.screen_ref)
        self._monitor = match_monitor(info, monitors)
        rects = snap_rects_on_screen(
            info, (self.width(), self.height()), monitors, windows, min_px=_MIN_SNAP_PX
        )
        self._snaps = [QRectF(*rect) for rect in rects]
        self.update()

    # -- input ---------------------------------------------------------------
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
            # The veil lifts for the drag: one full repaint.
            self.update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        self._pointer = event.position()
        if self._start is not None:
            self._end = event.position()
        self._owner.focus_on(self)
        self._repaint_changes()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton or self._start is None:
            return
        end = event.position()
        frac = selection_fractions(
            self._start.x(), self._start.y(), end.x(), end.y(), self.width(), self.height()
        )
        self._start = None
        self._end = None
        if frac is None:
            # A click, not a drag: take the window under the pointer, if any.
            snap = self._snap_at(end)
            if snap is not None:
                frac = (
                    snap.x() / self.width(),
                    snap.y() / self.height(),
                    snap.width() / self.width(),
                    snap.height() / self.height(),
                )
        if frac is None:
            self.update()
            return
        self._owner.finish(self, frac)

    def clear_pointer(self) -> None:
        if self._pointer is not None or self._start is not None:
            self._pointer = None
            self._start = None
            self._end = None
            self.update()

    # -- geometry ------------------------------------------------------------
    def _selection(self) -> QRectF | None:
        if self._start is None or self._end is None:
            return None
        return QRectF(self._start, self._end).normalized()

    def _snap_at(self, point: QPointF) -> QRectF | None:
        for rect in self._snaps:
            if rect.contains(point):
                return rect
        return None

    def _hole(self) -> QRectF | None:
        sel = self._selection()
        if sel is not None:
            return sel
        if self._pointer is not None:
            return self._snap_at(self._pointer)
        return None

    def _scale(self) -> float:
        """Device pixels per logical pixel — from the frozen frame when present."""
        if self._frozen is not None and self.width() > 0:
            return self._frozen.width() / self.width()
        return float(self.screen_ref.devicePixelRatio() or 1.0)

    def _capture_point(self, point: QPointF) -> tuple[int, int]:
        """``point`` in capture coordinates (what the screenshot will use)."""
        mon = self._monitor
        if mon is None:
            scale = self._scale()
            return round(point.x() * scale), round(point.y() * scale)
        fx = point.x() / max(1.0, self.width())
        fy = point.y() / max(1.0, self.height())
        return (
            round(float(mon.get("left", 0)) + fx * float(mon.get("width", 0))),
            round(float(mon.get("top", 0)) + fy * float(mon.get("height", 0))),
        )

    def _numbers(self) -> tuple[str, str] | None:
        """The two stacked numbers: position, or width and height while dragging."""
        sel = self._selection()
        if sel is not None:
            scale = self._scale()
            return str(round(sel.width() * scale)), str(round(sel.height() * scale))
        if self._pointer is None:
            return None
        x, y = self._capture_point(self._pointer)
        return str(x), str(y)

    def _numbers_rect(self) -> QRectF | None:
        """Where the numbers go: below-right of the crosshair, flipped at edges."""
        point = self._end if self._end is not None else self._pointer
        numbers = self._numbers()
        if point is None or numbers is None:
            return None
        w = max(self._metrics.horizontalAdvance(n) for n in numbers) + 4.0
        line = self._metrics.height()
        h = line * 2 + 2.0
        x = point.x() + _NUMBER_OFFSET.x()
        y = point.y() + _NUMBER_OFFSET.y()
        if x + w > self.width() - _EDGE_GAP:
            x = point.x() - _NUMBER_OFFSET.x() - w
        if y + h > self.height() - _EDGE_GAP:
            y = point.y() - _NUMBER_OFFSET.y() - h
        return QRectF(round(x), round(y), round(w), round(h))

    def _repaint_changes(self) -> None:
        """Repaint the old and new numbers, and the hole only when it changed."""
        numbers = self._numbers_rect()
        hole = self._hole()
        dirty = QRectF()
        for part in (numbers, self._painted_numbers):
            if part is not None:
                dirty = dirty.united(part.adjusted(-3, -3, 3, 3))
        if hole != self._painted_hole:
            for part in (hole, self._painted_hole):
                if part is not None:
                    dirty = dirty.united(part.adjusted(-3, -3, 3, 3))
        self._painted_numbers = numbers
        self._painted_hole = hole
        if not dirty.isEmpty():
            self.update(dirty.toAlignedRect())

    # -- painting ------------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setClipRect(event.rect())
        full = QRectF(0, 0, self.width(), self.height())
        if self._frozen is not None:
            painter.drawPixmap(full, self._frozen, QRectF(self._frozen.rect()))
        hole = self._hole()
        if self._selection() is not None and hole is not None:
            # Dragging: the surroundings stay clear, the selection turns grey.
            painter.fillRect(hole, _SELECTION_TINT)
        else:
            dim = QPainterPath()
            dim.addRect(full)
            if hole is not None:
                cut = QPainterPath()
                cut.addRect(hole)
                dim = dim.subtracted(cut)
            painter.fillPath(dim, _DIM_IDLE)
        if hole is not None:
            self._paint_border(painter, hole)
        self._paint_numbers(painter)
        painter.end()

    def _paint_border(self, painter: QPainter, rect: QRectF) -> None:
        # Drawn without antialiasing so both hairlines land on whole pixels.
        r = rect.adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(_BORDER_EDGE, 1.0))
        painter.drawRect(r.adjusted(-1.0, -1.0, 1.0, 1.0))
        painter.setPen(QPen(_BORDER, 1.0))
        painter.drawRect(r)

    def _paint_numbers(self, painter: QPainter) -> None:
        numbers = self._numbers()
        box = self._numbers_rect()
        if numbers is None or box is None:
            return
        line = self._metrics.height()
        ascent = self._metrics.ascent()
        path = QPainterPath()
        for i, text in enumerate(numbers):
            # Right-aligned in their column, the way macOS stacks them.
            x = box.right() - 2.0 - self._metrics.horizontalAdvance(text)
            path.addText(QPointF(x, box.top() + 1.0 + ascent + i * line), self._font, text)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        halo = QPen(_HALO, 2.6)
        halo.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.strokePath(path, halo)
        painter.fillPath(path, _INK)
        painter.restore()


class Picker(QObject):
    """Owns the per-screen windows and reports exactly one result."""

    def __init__(self, app: QApplication) -> None:
        super().__init__()
        self._app = app
        self._windows: list[_SelectWindow] = []
        self._focused: _SelectWindow | None = None
        self._done = False

    def start(self) -> None:
        screens = QGuiApplication.screens()
        # Freeze every screen BEFORE any overlay window exists.
        frozen = {id(s): self._grab(s) for s in screens}
        self._windows = [_SelectWindow(s, frozen[id(s)], self) for s in screens]
        cursor_screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        self._focused = next(
            (w for w in self._windows if w.screen_ref is cursor_screen),
            self._windows[0] if self._windows else None,
        )
        for win in self._windows:
            win.show()
        if self._focused is not None:
            self._focused.raise_()
            self._focused.activateWindow()
            self._focused.setFocus()
        self._app.processEvents()
        _emit({"event": wire.EVENT_READY})

    @staticmethod
    def _grab(screen) -> QPixmap | None:
        try:
            pixmap = screen.grabWindow(0)
        except Exception:  # noqa: BLE001 - no frozen frame: fall back to a live dim layer
            sys.stderr.write("appshot-picker: screen grab failed; using a live overlay\n")
            return None
        return None if pixmap.isNull() or pixmap.width() <= 0 else pixmap

    def set_layout(self, monitors: list[dict], windows: list[list[int]]) -> None:
        for win in self._windows:
            win.set_layout(monitors, windows)

    def focus_on(self, window: _SelectWindow) -> None:
        """The pointer moved onto ``window``: only that screen shows the numbers."""
        if self._focused is window:
            return
        for other in self._windows:
            if other is not window:
                other.clear_pointer()
        self._focused = window

    def finish(self, window: _SelectWindow | None, frac) -> None:
        if self._done:
            return
        self._done = True
        info = _screen_info(window.screen_ref) if window is not None else None
        for win in self._windows:
            win.hide()
        # The overlay must be off the glass before the parent grabs.
        self._app.processEvents()
        if info is None or frac is None:
            _emit({"event": wire.EVENT_SELECTION, "cancelled": True})
        else:
            _emit({"event": wire.EVENT_SELECTION, "screen": info, "rect": list(frac)})
        self._app.quit()

    @Slot(str)
    def on_line(self, raw: str) -> None:
        payload = wire.decode(raw)
        if payload is None:
            return
        cmd = payload.get("cmd")
        if cmd == wire.CMD_CANCEL:
            self.finish(None, None)
        elif cmd == wire.CMD_LAYOUT:
            monitors = payload.get("monitors")
            windows = payload.get("windows")
            if isinstance(monitors, list) and isinstance(windows, list):
                self.set_layout([m for m in monitors if isinstance(m, dict)], windows)

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


def run() -> int:
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
    picker = Picker(app)
    pump = _StdinPump()
    pump.line.connect(picker.on_line, Qt.ConnectionType.QueuedConnection)
    pump.eof.connect(picker.on_eof, Qt.ConnectionType.QueuedConnection)
    picker.start()
    pump.start()
    return app.exec()


__all__ = ["Picker", "crosshair_cursor", "run"]
