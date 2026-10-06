"""PySide6 renderer for the Jarvis X overlay sidecar.

Runs ONLY inside ``python -m jarvis.jarvisx.overlay``; the main process never
imports this module (AP-26). Four surfaces, all frameless and always on top:

- **Selection** — one dimmed window per screen with a crosshair; drag a
  rectangle (live size readout in real pixels), release to confirm, Esc or
  right-click to cancel. The selection is reported as fractions of its screen
  (see ``jarvis.jarvisx.geometry``), so mixed-DPI layouts stay exact.
- **Flash + fly** — the appshot shutter language: a white flash over the
  captured area, then the picture shrinks into the bottom-right corner.
- **Cards** — where the picture lands: a clickable thumbnail (opens the
  editor), a close (x) on hover, draggable, stacked newest-lowest, fading
  after the configured time unless it persists or the pointer rests on it.
- **Recording chrome** — a thin border just outside the recorded area and a
  "Stop" pill with a running timer, placed outside the area where possible.

Cards, border and pill are excluded from screen capture where the OS allows
(Windows display affinity, macOS window sharing); the controller also hides
cards around its own screenshots, and the recording border sits outside the
recorded area, so none of them ends up in a capture.
"""

from __future__ import annotations

import math
import os
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from contextlib import suppress
from typing import Any

from PySide6.QtCore import (
    QByteArray,
    QObject,
    QPoint,
    QPointF,
    QRectF,
    Qt,
    QTimer,
    QVariantAnimation,
    Signal,
    Slot,
)
from PySide6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QFontMetricsF,
    QGuiApplication,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import QApplication, QWidget

from jarvis.jarvisx import geometry
from jarvis.jarvisx.overlay import protocol

# Jarvis gold (matches the Computer-Use indicator and the orb border).
_GOLD = (255, 229, 0)
_SOFT_GOLD = (231, 196, 110)

_DIM_ALPHA = 105
_FADE_MS = 260

# Appshot shutter timings (see jarvis/cu/indicator/renderer.py) — the same
# visual language, ending in a resting card instead of sliding away.
_FLASH_MS = 200
_FLY_START_MS = 90
_FLY_MS = 430
_FLY_TOTAL_MS = _FLY_START_MS + _FLY_MS

_CARD_W = 300.0
_CARD_MAX_H = 220.0
_CARD_PAD = 14.0  # transparent margin that holds the shadow and the (x)
_CARD_RADIUS = 12.0
_CARD_MARGIN = 28.0
_CARD_GAP = 10.0
_CLOSE_R = 11.0
_DRAG_SLOP_PX = 5


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _ease_out_cubic(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return 1.0 - (1.0 - x) ** 3


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _screen_info(screen) -> dict[str, float]:
    g = screen.geometry()
    return {
        "x": float(g.x()),
        "y": float(g.y()),
        "w": float(g.width()),
        "h": float(g.height()),
        "dpr": float(screen.devicePixelRatio() or 1.0),
    }


def _screen_for_monitor(monitor: Any):
    """The QScreen that is the capture-space ``monitor`` ([l, t, w, h])."""
    screens = QGuiApplication.screens()
    primary = QGuiApplication.primaryScreen()
    if not screens or not isinstance(monitor, list) or len(monitor) != 4:
        return primary
    mon = {"left": monitor[0], "top": monitor[1], "width": monitor[2], "height": monitor[3]}
    return min(screens, key=lambda s: geometry.screen_monitor_score(_screen_info(s), mon))


def _frac(rect: Any) -> tuple[float, float, float, float] | None:
    if not isinstance(rect, list) or len(rect) != 4:
        return None
    try:
        fx, fy, fw, fh = (max(0.0, min(1.0, float(v))) for v in rect)
    except (TypeError, ValueError):  # Reject malformed region coordinates before rendering.
        return None
    return (fx, fy, fw, fh)


def _base_flags(*, click_through: bool) -> Qt.WindowType:
    flags = (
        Qt.WindowType.FramelessWindowHint
        | Qt.WindowType.WindowStaysOnTopHint
        | Qt.WindowType.Tool
        | Qt.WindowType.NoDropShadowWindowHint
    )
    if click_through:
        flags |= Qt.WindowType.WindowTransparentForInput | Qt.WindowType.WindowDoesNotAcceptFocus
    return flags


def _prepare(widget: QWidget, *, click_through: bool, activates: bool = False) -> None:
    widget.setWindowFlags(_base_flags(click_through=click_through))
    widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    if click_through:
        widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    if not activates:
        widget.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    if sys.platform == "darwin":
        # Qt::Tool is an NSPanel on macOS; without this it stays invisible
        # while this (never activated) process is in the background.
        always_show = getattr(Qt.WidgetAttribute, "WA_MacAlwaysShowToolWindow", None)
        if always_show is not None:
            widget.setAttribute(always_show)


def _exclude_from_capture(widget: QWidget) -> None:
    """Keep chrome out of every screenshot and recording where the OS can."""
    if os.environ.get("JARVIS_JARVISX_OVERLAY_CAPTURABLE", "").strip() in {"1", "true", "yes"}:
        return  # live verification needs to SEE the chrome in a screenshot
    try:
        if sys.platform == "win32":
            from jarvis.platform.capture_exclusion import exclude_hwnd_from_capture  # noqa: PLC0415

            exclude_hwnd_from_capture(int(widget.winId()))
        elif sys.platform == "darwin":
            from jarvis.platform.capture_exclusion import exclude_macos_app_windows  # noqa: PLC0415

            exclude_macos_app_windows()
    except Exception:  # noqa: BLE001 - cosmetic; the controller also hides cards
        _log("capture exclusion failed")


def _log(message: str) -> None:
    with suppress(Exception):
        sys.stderr.write(f"jarvisx-overlay: {message}\n")
        sys.stderr.flush()


# --------------------------------------------------------------------------
# Selection
# --------------------------------------------------------------------------


class _SelectWindow(QWidget):
    """One screen's dimmed selection canvas."""

    def __init__(self, screen, hint: str, owner: Renderer) -> None:
        super().__init__(None)
        _prepare(self, click_through=False, activates=True)
        self.setScreen(screen)
        self.setGeometry(screen.geometry())
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._screen = screen
        self._hint = hint
        self._owner = owner
        self._start: QPointF | None = None
        self._end: QPointF | None = None
        self._pointer: QPointF | None = None

    @property
    def info(self) -> dict[str, float]:
        return _screen_info(self._screen)

    def showEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().showEvent(event)
        _exclude_from_capture(self)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self._owner.finish_selection(None, None)
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.RightButton:
            self._owner.finish_selection(None, None)
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._start = event.position()
            self._end = event.position()
            self._owner.selection_started(self)
            self.update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        self._pointer = event.position()
        if self._start is not None:
            self._end = event.position()
        self._owner.pointer_moved(self)
        self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton or self._start is None:
            return
        end = event.position()
        frac = geometry.selection_fractions(
            self._start.x(), self._start.y(), end.x(), end.y(), self.width(), self.height()
        )
        self._start = None
        self._owner.finish_selection(self, frac)

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
        if self._hint and self._owner.hint_screen() is self:
            self._paint_hint(painter)
        painter.end()

    def _paint_size(self, painter: QPainter, sel: QRectF) -> None:
        dpr = float(self._screen.devicePixelRatio() or 1.0)
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

    def _paint_hint(self, painter: QPainter) -> None:
        font = QFont()
        font.setPointSizeF(10.5)
        font.setWeight(QFont.Weight.Medium)
        painter.setFont(font)
        metrics = QFontMetricsF(font)
        w = metrics.horizontalAdvance(self._hint) + 32.0
        h = metrics.height() + 14.0
        box = QRectF((self.width() - w) / 2.0, 24.0, w, h)
        painter.setPen(QPen(QColor(*_SOFT_GOLD, 200), 1.0))
        painter.setBrush(QColor(18, 18, 18, 190))
        painter.drawRoundedRect(box, h / 2.0, h / 2.0)
        painter.setPen(QColor(255, 240, 200, 240))
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, self._hint)


# --------------------------------------------------------------------------
# Flash + fly
# --------------------------------------------------------------------------


class _FlyWindow(QWidget):
    """Monitor-sized, click-through: flash the area, fly it to ``dst``."""

    def __init__(
        self,
        screen,
        src: QRectF,
        dst: QRectF,
        thumb: QImage,
        on_landed: Callable[[_FlyWindow], None],
    ) -> None:
        super().__init__(None)
        _prepare(self, click_through=True)
        self.setScreen(screen)
        self.setGeometry(screen.geometry())
        self._src = src
        self._dst = dst
        self._thumb = thumb
        self._on_landed: Callable[[_FlyWindow], None] | None = on_landed
        self._t = 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(float(_FLY_TOTAL_MS))
        self._anim.setDuration(_FLY_TOTAL_MS)
        self._anim.valueChanged.connect(self._tick)
        self._anim.finished.connect(self._landed)

    def start(self) -> None:
        self.show()
        self._anim.start()

    def retarget(self, dst: QRectF) -> None:
        self._dst = dst

    def finish_now(self) -> None:
        self._anim.stop()
        self._landed()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        _exclude_from_capture(self)

    def _tick(self, value) -> None:
        self._t = float(value)
        self.update()

    def _landed(self) -> None:
        callback, self._on_landed = self._on_landed, None
        self.hide()
        if callback is not None:
            callback(self)
        self.deleteLater()

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        t = self._t
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        fly = _ease_out_cubic((t - _FLY_START_MS) / _FLY_MS)
        rect = QRectF(
            _lerp(self._src.x(), self._dst.x(), fly),
            _lerp(self._src.y(), self._dst.y(), fly),
            _lerp(self._src.width(), self._dst.width(), fly),
            _lerp(self._src.height(), self._dst.height(), fly),
        )
        radius = _CARD_RADIUS * fly
        if not self._thumb.isNull():
            if fly > 0.0:
                for spread, alpha in ((10.0, 18), (5.0, 30), (2.0, 46)):
                    shadow = rect.adjusted(-spread, -spread + 4, spread, spread + 4)
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(QColor(0, 0, 0, int(alpha * fly)))
                    painter.drawRoundedRect(shadow, radius + spread, radius + spread)
            clip = QPainterPath()
            clip.addRoundedRect(rect, radius, radius)
            painter.save()
            painter.setClipPath(clip)
            painter.drawImage(rect, self._thumb)
            painter.restore()
            if fly > 0.0:
                pen = QPen(QColor(255, 255, 255, int(235 * fly)))
                pen.setWidthF(2.5)
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(rect, radius, radius)
        flash = 1.0 - _ease_out_cubic(t / _FLASH_MS)
        if flash > 0.0:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 255, 255, int(225 * flash)))
            painter.drawRect(self._src)
        painter.end()


# --------------------------------------------------------------------------
# Cards
# --------------------------------------------------------------------------


class _CardWindow(QWidget):
    """A resting, clickable thumbnail of one capture."""

    def __init__(
        self,
        owner: Renderer,
        item_id: str,
        screen,
        thumb: QImage,
        card_size: tuple[float, float],
        *,
        persist: bool,
        dismiss_s: int,
        badge: str,
    ) -> None:
        super().__init__(None)
        _prepare(self, click_through=False)
        self.setScreen(screen)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.item_id = item_id
        self.screen_ref = screen
        self.card_size = card_size
        self.moved_by_user = False
        self._owner = owner
        self._thumb = thumb
        self._badge = badge
        self._hover = False
        self._press_global: QPoint | None = None
        self._press_origin: QPoint | None = None
        self._dragging = False
        self._opacity_anim: QVariantAnimation | None = None
        self._leaving = False
        self.timer = geometry.CardTimer(persist=persist, dismiss_s=dismiss_s, now=time.monotonic())
        w, h = card_size
        self.resize(int(math.ceil(w + 2 * _CARD_PAD)), int(math.ceil(h + 2 * _CARD_PAD)))

    # -- placement ---------------------------------------------------------
    def place_card_at(self, x: float, y: float) -> None:
        """Move so the card itself (not its padding) starts at screen-local x, y."""
        g = self.screen_ref.geometry()
        self.move(int(g.x() + x - _CARD_PAD), int(g.y() + y - _CARD_PAD))

    def card_rect_local(self) -> QRectF:
        return QRectF(_CARD_PAD, _CARD_PAD, self.card_size[0], self.card_size[1])

    def _close_center(self) -> QPointF:
        return QPointF(_CARD_PAD + 2.0, _CARD_PAD + 2.0)

    def _on_close(self, pos: QPointF) -> bool:
        c = self._close_center()
        return self._hover and math.hypot(pos.x() - c.x(), pos.y() - c.y()) <= _CLOSE_R + 3.0

    # -- lifecycle -----------------------------------------------------------
    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        _exclude_from_capture(self)

    def leave(self, *, notify: bool) -> None:
        """Fade out and remove; ``notify`` reports a user dismissal."""
        if self._leaving:
            return
        self._leaving = True
        self._owner.card_leaving(self, notify=notify)
        anim = QVariantAnimation(self)
        anim.setStartValue(self.windowOpacity())
        anim.setEndValue(0.0)
        anim.setDuration(_FADE_MS)
        anim.valueChanged.connect(lambda v: self.setWindowOpacity(float(v)))
        anim.finished.connect(self._gone)
        self._opacity_anim = anim
        anim.start()

    def _gone(self) -> None:
        self.hide()
        self.deleteLater()

    # -- input ---------------------------------------------------------------
    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self.timer.hover(True, time.monotonic())
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self.timer.hover(False, time.monotonic())
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._press_global = event.globalPosition().toPoint()
        self._press_origin = self.pos()
        self._dragging = False

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        self.update()
        if self._press_global is None or self._press_origin is None:
            return
        delta = event.globalPosition().toPoint() - self._press_global
        if not self._dragging and delta.manhattanLength() > _DRAG_SLOP_PX:
            self._dragging = True
            self.moved_by_user = True
        if self._dragging:
            self.move(self._press_origin + delta)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton or self._press_global is None:
            return
        dragged = self._dragging
        self._press_global = None
        self._press_origin = None
        self._dragging = False
        if dragged or self._leaving:
            return
        if self._on_close(event.position()):
            self.leave(notify=True)
            return
        if self.card_rect_local().contains(event.position()):
            _emit(protocol.EVENT_CARD_CLICK, id=self.item_id)
            self.leave(notify=False)

    # -- painting ------------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        rect = self.card_rect_local()
        for spread, alpha in ((10.0, 18), (5.0, 30), (2.0, 46)):
            shadow = rect.adjusted(-spread, -spread + 4, spread, spread + 4)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(0, 0, 0, alpha))
            painter.drawRoundedRect(shadow, _CARD_RADIUS + spread, _CARD_RADIUS + spread)
        clip = QPainterPath()
        clip.addRoundedRect(rect, _CARD_RADIUS, _CARD_RADIUS)
        painter.save()
        painter.setClipPath(clip)
        if self._thumb.isNull():
            painter.fillRect(rect, QColor(40, 40, 40))
        else:
            painter.drawImage(rect, self._thumb)
        if self._hover:
            painter.fillRect(rect, QColor(0, 0, 0, 70))
        painter.restore()
        pen = QPen(QColor(255, 255, 255, 235))
        pen.setWidthF(2.5)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect, _CARD_RADIUS, _CARD_RADIUS)
        if self._badge:
            self._paint_badge(painter, rect)
        if self._hover:
            self._paint_edit_hint(painter, rect)
            self._paint_close(painter)
        painter.end()

    def _pill(self, painter: QPainter, box: QRectF, text: str) -> None:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(18, 18, 18, 200))
        painter.drawRoundedRect(box, box.height() / 2.0, box.height() / 2.0)
        painter.setPen(QColor(255, 255, 255, 240))
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, text)

    def _paint_badge(self, painter: QPainter, rect: QRectF) -> None:
        font = QFont()
        font.setPointSizeF(8.5)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        text = f"▶  {self._badge}"
        metrics = QFontMetricsF(font)
        box = QRectF(
            rect.left() + 8.0,
            rect.bottom() - metrics.height() - 14.0,
            metrics.horizontalAdvance(text) + 16.0,
            metrics.height() + 6.0,
        )
        self._pill(painter, box, text)

    def _paint_edit_hint(self, painter: QPainter, rect: QRectF) -> None:
        font = QFont()
        font.setPointSizeF(9.5)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        text = self._owner.text("edit")
        metrics = QFontMetricsF(font)
        w = metrics.horizontalAdvance(text) + 22.0
        h = metrics.height() + 10.0
        box = QRectF(rect.center().x() - w / 2.0, rect.center().y() - h / 2.0, w, h)
        self._pill(painter, box, text)

    def _paint_close(self, painter: QPainter) -> None:
        c = self._close_center()
        painter.setPen(QPen(QColor(255, 255, 255, 230), 1.2))
        painter.setBrush(QColor(28, 28, 28, 235))
        painter.drawEllipse(c, _CLOSE_R, _CLOSE_R)
        pen = QPen(QColor(255, 255, 255, 240))
        pen.setWidthF(1.8)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        d = _CLOSE_R * 0.42
        painter.drawLine(QPointF(c.x() - d, c.y() - d), QPointF(c.x() + d, c.y() + d))
        painter.drawLine(QPointF(c.x() - d, c.y() + d), QPointF(c.x() + d, c.y() - d))


# --------------------------------------------------------------------------
# Recording chrome
# --------------------------------------------------------------------------


class _BorderWindow(QWidget):
    """Click-through outline drawn just OUTSIDE the recorded area."""

    def __init__(self, screen, area: QRectF) -> None:
        super().__init__(None)
        _prepare(self, click_through=True)
        self.setScreen(screen)
        self.setGeometry(screen.geometry())
        self._area = area

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        _exclude_from_capture(self)

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(*_GOLD, 230))
        pen.setWidthF(2.0)
        pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(self._area.adjusted(-3.0, -3.0, 3.0, 3.0))
        painter.end()


class _PillWindow(QWidget):
    """The "Stop ● 00:12" pill; a click stops the recording."""

    def __init__(self, owner: Renderer, screen, started: float) -> None:
        super().__init__(None)
        _prepare(self, click_through=False)
        self.setScreen(screen)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._owner = owner
        self._started = started
        self._hover = False
        font = QFont()
        font.setPointSizeF(10.0)
        font.setWeight(QFont.Weight.DemiBold)
        self._font = font
        metrics = QFontMetricsF(font)
        sample = f"{owner.text('stop')}   00:00:00"
        self.resize(int(metrics.horizontalAdvance(sample) + 58), int(metrics.height() + 18))
        self._tick = QTimer(self)
        self._tick.setInterval(250)
        self._tick.timeout.connect(self.update)
        self._tick.start()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        _exclude_from_capture(self)

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            _emit(protocol.EVENT_STOP_CLICKED)

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        box = QRectF(1.0, 1.0, self.width() - 2.0, self.height() - 2.0)
        painter.setPen(QPen(QColor(*_SOFT_GOLD, 210), 1.0))
        painter.setBrush(QColor(18, 18, 18, 235 if self._hover else 210))
        painter.drawRoundedRect(box, box.height() / 2.0, box.height() / 2.0)
        elapsed = time.monotonic() - self._started
        pulse = 0.55 + 0.45 * (0.5 + 0.5 * math.cos(elapsed * math.pi))
        dot = QPointF(box.left() + 18.0, box.center().y())
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(235, 64, 52, int(255 * pulse)))
        painter.drawEllipse(dot, 5.5, 5.5)
        painter.setFont(self._font)
        painter.setPen(QColor(255, 244, 214, 245))
        text = f"{self._owner.text('stop')}   {geometry.format_elapsed(elapsed)}"
        painter.drawText(
            box.adjusted(32.0, 0.0, -12.0, 0.0),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            text,
        )
        painter.end()


# --------------------------------------------------------------------------
# Renderer
# --------------------------------------------------------------------------


class Renderer(QObject):
    """Owns every overlay surface and applies the controller's commands."""

    def __init__(self, app: QApplication) -> None:
        super().__init__()
        self._app = app
        self._commands: deque[dict] = deque()
        self._draining = False
        self._texts: dict[str, str] = {"edit": "Click to edit", "stop": "Stop"}
        # selection
        self._select_req: int | None = None
        self._select_windows: list[_SelectWindow] = []
        self._hint_window: _SelectWindow | None = None
        # cards, newest first
        self._cards: list[_CardWindow] = []
        self._flights: dict[str, tuple[_FlyWindow, _CardWindow]] = {}
        self._blanked = False
        # recording
        self._border: _BorderWindow | None = None
        self._pill: _PillWindow | None = None
        self._expiry = QTimer(self)
        self._expiry.setInterval(250)
        self._expiry.timeout.connect(self._expire_cards)
        self._expiry.start()

    def text(self, key: str) -> str:
        return self._texts.get(key, key)

    # -- IPC -----------------------------------------------------------------
    @Slot(str)
    def on_line(self, raw: str) -> None:
        payload = protocol.decode_command(raw)
        if payload is None:
            return
        self._commands.append(payload)
        if not self._draining:
            self._drain()

    def _drain(self) -> None:
        self._draining = True
        try:
            while self._commands:
                payload = self._commands.popleft()
                cmd = payload["cmd"]
                texts = payload.get("texts")
                if isinstance(texts, dict):
                    self._texts.update({str(k): str(v) for k, v in texts.items()})
                try:
                    self._apply(cmd, payload)
                except Exception as exc:  # noqa: BLE001 - one bad command must not kill the overlay
                    _log(f"{cmd} failed: {type(exc).__name__}: {exc}")
                if cmd == protocol.CMD_QUIT:
                    _ack(cmd)
                    self._app.quit()
                    self._commands.clear()
                    return
                # Flush native show/hide work before acknowledging, so an ACK
                # of "blank" really means "not on screen any more".
                self._app.processEvents()
                _ack(cmd)
        finally:
            self._draining = False

    def _apply(self, cmd: str, payload: dict) -> None:
        if cmd == protocol.CMD_SELECT:
            self._start_selection(payload)
        elif cmd == protocol.CMD_CANCEL_SELECT:
            self.finish_selection(None, None)
        elif cmd == protocol.CMD_CARD:
            self._add_card(payload)
        elif cmd == protocol.CMD_REMOVE_CARD:
            for card in list(self._cards):
                if card.item_id == str(payload.get("id", "")):
                    card.leave(notify=False)
        elif cmd == protocol.CMD_BLANK:
            self._set_blank(True)
        elif cmd == protocol.CMD_UNBLANK:
            self._set_blank(False)
        elif cmd == protocol.CMD_REC_SHOW:
            self._show_recording(payload)
        elif cmd == protocol.CMD_REC_HIDE:
            self._hide_recording()

    # -- selection -----------------------------------------------------------
    def _start_selection(self, payload: dict) -> None:
        if self._select_req is not None:
            self.finish_selection(None, None)
        try:
            self._select_req = int(payload.get("req", 0))
        except (TypeError, ValueError):  # An invalid request identifier cannot correlate a selection result.
            self._select_req = 0
        hint = str(payload.get("hint", ""))
        self._select_windows = [_SelectWindow(s, hint, self) for s in QGuiApplication.screens()]
        cursor_screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        self._hint_window = next(
            (w for w in self._select_windows if w._screen is cursor_screen), None
        )
        for win in self._select_windows:
            win.show()
        focus = self._hint_window or (self._select_windows[0] if self._select_windows else None)
        if focus is not None:
            focus.raise_()
            focus.activateWindow()
            focus.setFocus()

    def hint_screen(self) -> _SelectWindow | None:
        return self._hint_window

    def selection_started(self, window: _SelectWindow) -> None:
        for other in self._select_windows:
            if other is not window:
                other.clear_pointer()
        if self._hint_window is not window:
            self._hint_window = window
            for win in self._select_windows:
                win.update()

    def pointer_moved(self, window: _SelectWindow) -> None:
        for other in self._select_windows:
            if other is not window:
                other.clear_pointer()

    def finish_selection(
        self, window: _SelectWindow | None, frac: tuple[float, float, float, float] | None
    ) -> None:
        req = self._select_req
        if req is None:
            return
        self._select_req = None
        info = window.info if window is not None else None
        for win in self._select_windows:
            win.hide()
            win.deleteLater()
        self._select_windows = []
        self._hint_window = None
        # The dimmed overlay must be gone from the glass before the grab.
        self._app.processEvents()
        if info is None or frac is None:
            _emit(protocol.EVENT_SELECTION, req=req, cancelled=True)
            return
        _emit(protocol.EVENT_SELECTION, req=req, screen=info, rect=list(frac))

    # -- cards ---------------------------------------------------------------
    def _add_card(self, payload: dict) -> None:
        item_id = str(payload.get("id", ""))
        if not item_id:
            return
        thumb = QImage()
        raw = payload.get("thumb")
        if isinstance(raw, str) and raw:
            thumb.loadFromData(QByteArray.fromBase64(raw.encode("ascii")))
        screen = _screen_for_monitor(payload.get("monitor"))
        if screen is None:
            return
        aspect = thumb.height() / max(1, thumb.width()) if not thumb.isNull() else 0.6
        cw = _CARD_W
        ch = cw * aspect
        if ch > _CARD_MAX_H:
            ch = _CARD_MAX_H
            cw = ch / max(aspect, 1e-6)
        card = _CardWindow(
            self,
            item_id,
            screen,
            thumb,
            (cw, ch),
            persist=bool(payload.get("persist", False)),
            dismiss_s=geometry.clamp_dismiss_s(payload.get("dismiss_s", 30)),
            badge=str(payload.get("badge", "") or ""),
        )
        self._cards.insert(0, card)
        while len(self._cards) > geometry.MAX_CARDS:
            self._cards[-1].leave(notify=False)
        self._layout_cards()
        frac = _frac(payload.get("rect"))
        g = screen.geometry()
        if frac is None or self._blanked:
            self._land(card)
            return
        fx, fy, fw, fh = frac
        src = QRectF(
            fx * g.width(),
            fy * g.height(),
            max(1.0, fw * g.width()),
            max(1.0, fh * g.height()),
        )
        dst = self._card_local_rect(card)
        flight = _FlyWindow(screen, src, dst, thumb, lambda _w, c=card: self._land(c))
        self._flights[item_id] = (flight, card)
        flight.start()

    def _card_local_rect(self, card: _CardWindow) -> QRectF:
        g = card.screen_ref.geometry()
        return QRectF(
            card.x() + _CARD_PAD - g.x(),
            card.y() + _CARD_PAD - g.y(),
            card.card_size[0],
            card.card_size[1],
        )

    def _land(self, card: _CardWindow) -> None:
        self._flights.pop(card.item_id, None)
        if card not in self._cards:
            return
        card.timer = geometry.CardTimer(
            persist=card.timer.persist, dismiss_s=card.timer.dismiss_s, now=time.monotonic()
        )
        if not self._blanked:
            card.setWindowOpacity(1.0)
            card.show()

    def _layout_cards(self) -> None:
        """Stack the auto-placed cards per screen, newest lowest."""
        by_screen: dict[int, list[_CardWindow]] = {}
        for card in self._cards:
            if not card.moved_by_user:
                by_screen.setdefault(id(card.screen_ref), []).append(card)
        for cards in by_screen.values():
            g = cards[0].screen_ref.geometry()
            corners = geometry.card_stack(
                float(g.width()),
                float(g.height()),
                [c.card_size for c in cards],
                margin=_CARD_MARGIN,
                gap=_CARD_GAP + 2 * _CARD_PAD - _CARD_PAD,
            )
            for card, (x, y) in zip(cards, corners, strict=True):
                card.place_card_at(x, y)
                flight = self._flights.get(card.item_id)
                if flight is not None:
                    flight[0].retarget(self._card_local_rect(card))

    def card_leaving(self, card: _CardWindow, *, notify: bool) -> None:
        with suppress(ValueError):
            self._cards.remove(card)
        flight = self._flights.pop(card.item_id, None)
        if flight is not None:
            flight[0].finish_now()
        if notify:
            _emit(protocol.EVENT_CARD_CLOSED, id=card.item_id)
        self._layout_cards()

    def _expire_cards(self) -> None:
        now = time.monotonic()
        for card in list(self._cards):
            if card.item_id in self._flights:
                continue
            if card.timer.expired(now):
                card.leave(notify=False)

    def _set_blank(self, blank: bool) -> None:
        self._blanked = blank
        for flight, _card in list(self._flights.values()):
            flight.finish_now()
        for card in self._cards:
            if blank:
                card.hide()
            else:
                card.show()

    # -- recording -----------------------------------------------------------
    def _show_recording(self, payload: dict) -> None:
        self._hide_recording()
        screen = _screen_for_monitor(payload.get("monitor"))
        if screen is None:
            return
        g = screen.geometry()
        frac = _frac(payload.get("rect"))
        if frac is not None:
            fx, fy, fw, fh = frac
            area = QRectF(fx * g.width(), fy * g.height(), fw * g.width(), fh * g.height())
            self._border = _BorderWindow(screen, area)
            self._border.show()
        pill = _PillWindow(self, screen, time.monotonic())
        spot = geometry.pill_placement(
            frac, float(g.width()), float(g.height()), float(pill.width()), float(pill.height())
        )
        pill.move(int(g.x() + spot.x), int(g.y() + spot.y))
        pill.show()
        self._pill = pill

    def _hide_recording(self) -> None:
        for widget in (self._border, self._pill):
            if widget is not None:
                widget.hide()
                widget.deleteLater()
        self._border = None
        self._pill = None


# --------------------------------------------------------------------------
# Process plumbing
# --------------------------------------------------------------------------

_out_lock = threading.Lock()


def _write(line: str) -> None:
    # A failed write means the parent is gone; the stdin EOF path quits.
    with suppress(Exception), _out_lock:
        sys.stdout.write(line)
        sys.stdout.flush()


def _ack(cmd: str) -> None:
    _write(protocol.encode_ack(cmd))


def _emit(event: str, **fields: Any) -> None:
    _write(protocol.encode_event(event, **fields))


class _StdinPump(QObject):
    line = Signal(str)
    eof = Signal()

    def start(self) -> None:
        threading.Thread(target=self._run, name="jarvisx-overlay-stdin", daemon=True).start()

    def _run(self) -> None:
        with suppress(Exception):
            for raw in sys.stdin:
                self.line.emit(raw)
        self.eof.emit()


def run() -> int:
    """Sidecar main loop; returns the process exit code."""
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    try:
        app = QApplication(sys.argv[:1] or ["jarvisx-overlay"])
    except Exception as exc:  # noqa: BLE001 - no display / no platform plugin
        sys.stderr.write(f"jarvisx-overlay: no usable display ({exc!r}).\n")
        return protocol.EXIT_NO_GUI
    app.setQuitOnLastWindowClosed(False)
    renderer = Renderer(app)
    pump = _StdinPump()
    pump.line.connect(renderer.on_line, Qt.ConnectionType.QueuedConnection)
    pump.eof.connect(app.quit, Qt.ConnectionType.QueuedConnection)
    pump.start()
    return app.exec()


__all__ = ["Renderer", "run"]
