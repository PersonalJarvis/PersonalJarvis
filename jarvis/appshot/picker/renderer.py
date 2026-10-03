"""PySide6 area picker for region appshots, modelled on ShareX's region capture.

Runs ONLY inside ``python -m jarvis.appshot.picker``; the main process never
imports this module (AP-26). One frameless, always-on-top window per screen:

- **Frozen screen.** Each screen is grabbed once before the overlay appears
  and shown dimmed, so nothing moves under the selection. Where the grab
  fails (no permission, odd platform) the overlay falls back to a translucent
  dim layer over the live desktop and simply has no magnifier.
- **Window snapping.** Hovering highlights the window under the pointer
  (rectangles from the main process, top-most first); a click without a drag
  selects exactly that window.
- **Drag to select.** The selection is cut out of the dim layer with a
  marching-ants border and its size in real pixels.
- **Magnifier.** A round lens of zoomed pixels beside the pointer with the
  centre pixel outlined and a small pill underneath: the position (or the
  selection size) and the zoom. The mouse wheel zooms it; the last zoom is
  kept for the next pick (``QSettings``).

Esc or a right-click cancels. The result is reported as fractions of its
screen, so mixed-DPI layouts map back to capture pixels exactly (see
:mod:`jarvis.appshot.region`).
"""

from __future__ import annotations

import sys
import threading
from contextlib import suppress

from PySide6.QtCore import (
    QObject,
    QPointF,
    QRect,
    QRectF,
    QSettings,
    Qt,
    QTimer,
    Signal,
    Slot,
)
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
from jarvis.appshot.region import (
    MAG_BOX_PX,
    MAG_DEFAULT_ZOOM,
    MAG_ZOOMS,
    magnifier_layout,
    match_monitor,
    selection_fractions,
    snap_rects_on_screen,
    step_zoom,
)

_DIM = QColor(0, 0, 0, 115)
_LABEL_BG = QColor(18, 18, 20, 225)
_LABEL_FG = QColor(255, 255, 255, 240)
_LABEL_MUTED = QColor(255, 255, 255, 150)

#: Magnifier placement and its info strip.
_MAG_OFFSET = 20.0
_MAG_STRIP_H = 20.0
#: Pixel-grid lines only once a source pixel is at least this wide (logical px).
_MAG_GRID_MIN_CELL = 6.0

#: Marching ants: dash length and the tick that moves them.
_DASH = 4.0
_ANTS_MS = 90

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


def _font(size: float, *, bold: bool = False) -> QFont:
    font = QFont()
    font.setPointSizeF(size)
    font.setWeight(QFont.Weight.DemiBold if bold else QFont.Weight.Normal)
    return font


def _draw_label(painter: QPainter, box: QRectF, text: str, font: QFont) -> None:
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(_LABEL_BG)
    painter.drawRoundedRect(box, 4.0, 4.0)
    painter.setFont(font)
    painter.setPen(_LABEL_FG)
    painter.drawText(box, Qt.AlignmentFlag.AlignCenter, text)


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
        self.setCursor(Qt.CursorShape.CrossCursor)
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
            self.update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        self._pointer = event.position()
        if self._start is not None:
            self._end = event.position()
        self._owner.focus_on(self)
        self.update()

    def wheelEvent(self, event) -> None:  # noqa: N802
        delta = event.angleDelta().y()
        if delta:
            self._owner.zoom_by(1 if delta > 0 else -1)
        event.accept()

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

    def has_hole(self) -> bool:
        return self._hole() is not None

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

    # -- painting ------------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        full = QRectF(0, 0, self.width(), self.height())
        if self._frozen is not None:
            painter.drawPixmap(full, self._frozen, QRectF(self._frozen.rect()))
        hole = self._hole()
        dim = QPainterPath()
        dim.addRect(full)
        if hole is not None:
            cut = QPainterPath()
            cut.addRect(hole)
            dim = dim.subtracted(cut)
        painter.fillPath(dim, _DIM)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if hole is not None:
            self._paint_border(painter, hole)
            self._paint_size(painter, hole)
        if self._pointer is not None and self._frozen is not None:
            self._paint_magnifier(painter, self._pointer)
        if self._owner.hint and self._owner.hint_window is self:
            self._paint_hint(painter, self._owner.hint)
        painter.end()

    def _paint_border(self, painter: QPainter, rect: QRectF) -> None:
        r = rect.adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(0, 0, 0, 220), 1.0))
        painter.drawRect(r)
        ants = QPen(QColor(255, 255, 255, 250), 1.0)
        ants.setDashPattern([_DASH, _DASH])
        ants.setDashOffset(self._owner.ants_offset)
        painter.setPen(ants)
        painter.drawRect(r)

    def _paint_size(self, painter: QPainter, rect: QRectF) -> None:
        scale = self._scale()
        text = f"{round(rect.width() * scale)} × {round(rect.height() * scale)}"
        font = _font(9.0, bold=True)
        metrics = QFontMetricsF(font)
        w = metrics.horizontalAdvance(text) + 14.0
        h = metrics.height() + 6.0
        x = rect.left()
        y = rect.top() - h - 5.0
        if y < 4.0:
            x, y = rect.left() + 5.0, rect.top() + 5.0
        x = max(4.0, min(x, self.width() - w - 4.0))
        _draw_label(painter, QRectF(x, y, w, h), text, font)

    def _paint_magnifier(self, painter: QPainter, point: QPointF) -> None:
        frozen = self._frozen
        assert frozen is not None
        scale = self._scale()
        zoom = self._owner.zoom
        count, cell = magnifier_layout(zoom, scale)
        half = count // 2
        cx, cy = int(point.x() * scale), int(point.y() * scale)
        source = QRect(cx - half, cy - half, count, count)
        side = MAG_BOX_PX

        sel = self._selection()
        if sel is not None:
            info = f"{round(sel.width() * scale)} × {round(sel.height() * scale)}"
        else:
            px, py = self._capture_point(point)
            info = f"{px}, {py}"
        info = f"{info}   {zoom}×"
        font = _font(8.0)
        metrics = QFontMetricsF(font)
        pill_w = metrics.horizontalAdvance(info) + 16.0
        pill_h = _MAG_STRIP_H
        total_h = side + 6.0 + pill_h

        x = point.x() + _MAG_OFFSET
        y = point.y() + _MAG_OFFSET
        if x + side > self.width() - 2:
            x = point.x() - _MAG_OFFSET - side
        if y + total_h > self.height() - 2:
            y = point.y() - _MAG_OFFSET - total_h
        box = QRectF(round(x), round(y), side, side)

        painter.save()
        circle = QPainterPath()
        circle.addEllipse(box)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setClipPath(circle)
        painter.fillRect(box, QColor(0, 0, 0))
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        # The pixel grid is centred on the circle; its outer pixels are clipped.
        ox = box.x() + (side - count * cell) / 2.0
        oy = box.y() + (side - count * cell) / 2.0
        visible = source.intersected(frozen.rect())
        if not visible.isEmpty():
            target = QRectF(
                ox + (visible.x() - source.x()) * cell,
                oy + (visible.y() - source.y()) * cell,
                visible.width() * cell,
                visible.height() * cell,
            )
            painter.drawPixmap(target, frozen, QRectF(visible))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        if cell >= _MAG_GRID_MIN_CELL:
            painter.setPen(QPen(QColor(0, 0, 0, 38), 0))
            for i in range(1, count):
                gx, gy = round(ox + i * cell), round(oy + i * cell)
                painter.drawLine(QPointF(gx, box.y()), QPointF(gx, box.bottom()))
                painter.drawLine(QPointF(box.x(), gy), QPointF(box.right(), gy))
        centre = QRectF(round(ox + half * cell), round(oy + half * cell), round(cell), round(cell))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(0, 0, 0), 0))
        painter.drawRect(centre.adjusted(-1, -1, 0, 0))
        painter.setPen(QPen(QColor(255, 255, 255), 0))
        painter.drawRect(centre.adjusted(0, 0, -1, -1))
        painter.setClipping(False)

        # One crisp two-tone ring: white inside, a thin dark edge outside.
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(0, 0, 0, 170), 1.0))
        painter.drawEllipse(box.adjusted(-1.0, -1.0, 1.0, 1.0))
        painter.setPen(QPen(QColor(255, 255, 255, 240), 1.5))
        painter.drawEllipse(box.adjusted(0.75, 0.75, -0.75, -0.75))

        # Position (or size) and zoom in a small flat pill under the circle.
        pill = QRectF(box.center().x() - pill_w / 2.0, box.bottom() + 6.0, pill_w, pill_h)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_LABEL_BG)
        painter.drawRoundedRect(pill, pill_h / 2.0, pill_h / 2.0)
        painter.setFont(font)
        painter.setPen(_LABEL_FG)
        painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, info)
        painter.restore()

    def _paint_hint(self, painter: QPainter, hint: str) -> None:
        font = _font(9.0)
        metrics = QFontMetricsF(font)
        w = metrics.horizontalAdvance(hint) + 24.0
        h = metrics.height() + 10.0
        box = QRectF((self.width() - w) / 2.0, 16.0, w, h)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_LABEL_BG)
        painter.drawRoundedRect(box, 5.0, 5.0)
        painter.setFont(font)
        painter.setPen(_LABEL_MUTED)
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
        self.ants_offset = 0.0
        self._settings = QSettings("PersonalJarvis", "AppshotPicker")
        try:
            saved = int(self._settings.value("zoom", MAG_DEFAULT_ZOOM))
        except (TypeError, ValueError):  # a corrupt saved zoom falls back to the default
            saved = MAG_DEFAULT_ZOOM
        self.zoom = saved if saved in MAG_ZOOMS else MAG_DEFAULT_ZOOM
        self._ants = QTimer(self)
        self._ants.setInterval(_ANTS_MS)
        self._ants.timeout.connect(self._march)

    def start(self) -> None:
        screens = QGuiApplication.screens()
        # Freeze every screen BEFORE any overlay window exists.
        frozen = {id(s): self._grab(s) for s in screens}
        self._windows = [_SelectWindow(s, frozen[id(s)], self) for s in screens]
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
        self._ants.start()
        self._app.processEvents()
        _emit({"event": wire.EVENT_READY})

    @staticmethod
    def _grab(screen) -> QPixmap | None:
        try:
            from jarvis.appshot.region import preview_capture_allowed

            if not preview_capture_allowed():
                return None
            pixmap = screen.grabWindow(0)
        except Exception:  # noqa: BLE001 - no frozen frame: fall back to a live dim layer
            sys.stderr.write("appshot-picker: screen grab failed; using a live overlay\n")
            return None
        return None if pixmap.isNull() or pixmap.width() <= 0 else pixmap

    def _march(self) -> None:
        self.ants_offset = (self.ants_offset + 1.0) % (2 * _DASH)
        for win in self._windows:
            if win.has_hole():
                win.update()

    def zoom_by(self, steps: int) -> None:
        zoom = step_zoom(self.zoom, steps)
        if zoom == self.zoom:
            return
        self.zoom = zoom
        self._settings.setValue("zoom", zoom)
        for win in self._windows:
            win.update()

    def set_layout(self, monitors: list[dict], windows: list[list[int]]) -> None:
        for win in self._windows:
            win.set_layout(monitors, windows)

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
        self._ants.stop()
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
    picker.start()
    pump.start()
    return app.exec()


__all__ = ["Picker", "run"]
