"""PySide6 renderer for the Computer-Use screen indicator.

Runs ONLY inside the sidecar process (``python -m jarvis.cu.indicator``).
This module imports PySide6 at module level — the main Jarvis process must
never import it (boot-path discipline, AP-26); the ``__main__`` entry
guards the import and exits with ``protocol.EXIT_NO_GUI`` when the GUI
stack is unavailable.

Visual contract (maintainer-approved 2026-07-15):

- A soft gold glow along every edge of EVERY monitor, breathing on a
  ~2.4 s sine loop, 300 ms fade in/out.
- An "Esc to cancel" pill top-center on the primary monitor (text arrives
  pre-localized from the controller; omitted when Escape isn't armable).
- Frameless, always-on-top, fully click-through, never activates, and on
  Windows excluded from screen capture (see ``win32.py``).
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from collections import deque
from contextlib import suppress
from pathlib import Path

from PySide6.QtCore import (
    QByteArray,
    QEasingCurve,
    QMimeData,
    QObject,
    QPoint,
    QPointF,
    QRectF,
    Qt,
    QTimer,
    QUrl,
    QVariantAnimation,
    Signal,
    Slot,
)
from PySide6.QtGui import (
    QColor,
    QDrag,
    QFont,
    QFontMetricsF,
    QGuiApplication,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QApplication, QWidget

from jarvis.cu.indicator import protocol
from jarvis.cu.indicator.win32 import (
    exclude_from_capture,
    harden_clickable_window,
    harden_window,
)

# Jarvis gold — matches ui/orb BUBBLE_BORDER_HEX (#FFE500) at the crisp
# edge, falling off through the softer [ui].bar_accent gold (#e7c46e).
_EDGE_RGB = (255, 229, 0)
_SOFT_RGB = (231, 196, 110)

_GLOW_MIN_PX = 32
_GLOW_MAX_PX = 110
_EDGE_LINE_PX = 3

_PULSE_PERIOD_MS = 2400
_PULSE_FLOOR = 0.62  # breathing dims to 62 %, never fully out
_FADE_MS = 300


class _GlowWindow(QWidget):
    """One click-through glow window covering one monitor."""

    def __init__(self, screen, *, with_pill: bool, hint: str) -> None:
        super().__init__(None)
        self._with_pill = with_pill
        self._hint = hint
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.Tool
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        if sys.platform == "darwin":
            # Qt::Tool maps to NSPanel on macOS.  An NSPanel normally stays
            # off-screen while its process is inactive, but this sidecar is
            # deliberately never activated.  Without the always-show
            # attribute the animation advances and commands are acknowledged
            # while the native window remains invisible.
            mac_always_show = getattr(Qt.WidgetAttribute, "WA_MacAlwaysShowToolWindow", None)
            if mac_always_show is not None:
                self.setAttribute(mac_always_show)
        self.setScreen(screen)
        self.setGeometry(screen.geometry())
        self.setWindowOpacity(0.0)

    def set_hint(self, hint: str) -> None:
        if hint != self._hint:
            self._hint = hint
            self.update()

    # -- Windows hardening ------------------------------------------------
    def _apply_native_styles(self) -> None:
        hwnd = int(self.winId())
        harden_window(hwnd)
        exclude_from_capture(hwnd)

    def showEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().showEvent(event)
        # Reapply on EVERY show: Windows silently drops layered styles on
        # some style mutations (BUG-030 class), and blank/unblank cycles
        # re-show the window.
        self._apply_native_styles()

    # -- painting ----------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override)
        del event
        w = self.width()
        h = self.height()
        if w <= 0 or h <= 0:
            return
        glow = max(_GLOW_MIN_PX, min(_GLOW_MAX_PX, int(min(w, h) * 0.06)))

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)

        edge = QColor(*_EDGE_RGB, 205)
        soft = QColor(*_SOFT_RGB, 135)
        clear = QColor(*_SOFT_RGB, 0)

        def _edge_gradient(x1: float, y1: float, x2: float, y2: float):
            grad = QLinearGradient(x1, y1, x2, y2)
            grad.setColorAt(0.0, edge)
            grad.setColorAt(0.35, soft)
            grad.setColorAt(1.0, clear)
            return grad

        painter.fillRect(0, 0, w, glow, _edge_gradient(0, 0, 0, glow))
        painter.fillRect(0, h - glow, w, glow, _edge_gradient(0, h, 0, h - glow))
        painter.fillRect(0, 0, glow, h, _edge_gradient(0, 0, glow, 0))
        painter.fillRect(w - glow, 0, glow, h, _edge_gradient(w, 0, w - glow, 0))

        # Crisp definition line at the very edge.
        pen = QPen(QColor(*_EDGE_RGB, 235))
        pen.setWidth(_EDGE_LINE_PX)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        inset = _EDGE_LINE_PX // 2
        painter.drawRect(inset, inset, w - _EDGE_LINE_PX, h - _EDGE_LINE_PX)

        if self._with_pill and self._hint:
            self._paint_pill(painter, w)
        painter.end()

    def _paint_pill(self, painter: QPainter, w: int) -> None:
        font = QFont()
        font.setPointSizeF(10.5)
        font.setWeight(QFont.Weight.Medium)
        painter.setFont(font)
        metrics = QFontMetricsF(font)
        pad_x, pad_y = 16.0, 7.0
        text_w = metrics.horizontalAdvance(self._hint)
        pill_w = text_w + 2 * pad_x
        pill_h = metrics.height() + 2 * pad_y
        x = (w - pill_w) / 2.0
        y = 18.0
        radius = pill_h / 2.0

        painter.setPen(QPen(QColor(*_SOFT_RGB, 200), 1.0))
        painter.setBrush(QColor(18, 18, 18, 175))
        painter.drawRoundedRect(int(x), int(y), int(pill_w), int(pill_h), radius, radius)
        painter.setPen(QColor(255, 240, 200, 235))
        painter.drawText(
            int(x),
            int(y),
            int(pill_w),
            int(pill_h),
            Qt.AlignmentFlag.AlignCenter,
            self._hint,
        )


# ---------------------------------------------------------------------------
# Appshot shutter effect — the phone-screenshot moment.
#
# A white flash over the captured surface, then the picture shrinks into the
# bottom-right corner of that monitor. There it becomes a small interactive
# card (``_CardWindow``): hovering keeps it, a click asks the app to open the
# editor, a drag hands the finished picture to any app that takes a file, a
# right-click dismisses it. The flight canvas itself stays click-through.
# ---------------------------------------------------------------------------

_SNAP_FLASH_MS = 200
_SNAP_FLY_START_MS = 90
_SNAP_FLY_MS = 430
_SNAP_TOTAL_MS = _SNAP_FLY_START_MS + _SNAP_FLY_MS
_SNAP_THUMB_W = 320
_SNAP_THUMB_MAX_H = 240
_SNAP_MARGIN = 28
_SNAP_RADIUS = 12.0

#: The resting card: how long it stays untouched, and after the pointer left.
_CARD_REST_MS = 6000
_CARD_AFTER_HOVER_MS = 2500
_CARD_OUT_MS = 240
#: A press that moves further than this (logical px) is a drag, not a click.
_CARD_DRAG_SLOP = 6
#: How long a status line ("Copied") stays on the card.
_CARD_STATUS_MS = 1600
#: Hover buttons: the Copy / Save pills and the round Close / Edit chips.
_CARD_BUTTON_H = 28.0
_CARD_ROUND = 26.0
#: Drag files older than this are removed the next time one is written.
_DRAG_FILE_MAX_AGE_S = 3600


def _ease_out_cubic(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return 1.0 - (1.0 - x) ** 3


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _match_screen(monitor: list[float]):
    """The QScreen that best matches a capture-coordinate monitor rect.

    Capture coordinates are physical pixels on Windows and points on macOS,
    while Qt reports logical geometry — so every plausible interpretation of
    each screen is scored and the closest one wins. Primary on no data.
    """
    screens = QGuiApplication.screens()
    primary = QGuiApplication.primaryScreen()
    if not screens or len(monitor) != 4:
        return primary
    left, top, width, height = (float(v) for v in monitor)
    best, best_score = primary, float("inf")
    for screen in screens:
        g = screen.geometry()
        dpr = float(screen.devicePixelRatio() or 1.0)
        candidates = (
            (g.x(), g.y(), g.width(), g.height()),
            (g.x(), g.y(), g.width() * dpr, g.height() * dpr),
            (g.x() * dpr, g.y() * dpr, g.width() * dpr, g.height() * dpr),
        )
        for cx, cy, cw, ch in candidates:
            score = abs(cx - left) + abs(cy - top) + abs(cw - width) + abs(ch - height)
            if score < best_score:
                best, best_score = screen, score
    return best


def _paint_card(painter: QPainter, rect: QRectF, thumb: QImage, radius: float, ring: float) -> None:
    """The thumbnail with rounded corners and a white ring — flight and card."""
    clip = QPainterPath()
    clip.addRoundedRect(rect, radius, radius)
    painter.save()
    painter.setClipPath(clip)
    painter.drawImage(rect, thumb)
    painter.restore()
    if ring > 0.0:
        pen = QPen(QColor(255, 255, 255, int(235 * ring)))
        pen.setWidthF(2.5)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect, radius, radius)


class _SnapWindow(QWidget):
    """One monitor-sized, click-through canvas for the flash and the flight."""

    def __init__(self, screen, rect_frac: list[float], thumb: QImage, on_landed, on_done) -> None:
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.Tool
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        if sys.platform == "darwin":
            mac_always_show = getattr(Qt.WidgetAttribute, "WA_MacAlwaysShowToolWindow", None)
            if mac_always_show is not None:
                self.setAttribute(mac_always_show)
        self.setScreen(screen)
        self.setGeometry(screen.geometry())
        self._screen_geo = screen.geometry()
        self._thumb = thumb
        self._on_landed = on_landed
        self._on_done = on_done
        w, h = float(screen.geometry().width()), float(screen.geometry().height())
        fx, fy, fw, fh = (max(0.0, min(1.0, float(v))) for v in rect_frac)
        self._src = QRectF(fx * w, fy * h, max(1.0, fw * w), max(1.0, fh * h))
        aspect = (
            thumb.height() / max(1, thumb.width())
            if not thumb.isNull()
            else (fh * h) / max(fw * w, 1e-6)
        )
        tw = float(_SNAP_THUMB_W)
        th = tw * aspect
        if th > _SNAP_THUMB_MAX_H:
            th = float(_SNAP_THUMB_MAX_H)
            tw = th / max(aspect, 1e-6)
        # Land inside the work area, so the card never sits under the taskbar.
        avail = screen.availableGeometry()
        geo = screen.geometry()
        right = float(avail.right() + 1 - geo.x())
        bottom = float(avail.bottom() + 1 - geo.y())
        self._dst = QRectF(right - tw - _SNAP_MARGIN, bottom - th - _SNAP_MARGIN, tw, th)
        self._t = 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(float(_SNAP_TOTAL_MS))
        self._anim.setDuration(_SNAP_TOTAL_MS)
        self._anim.valueChanged.connect(self._on_tick)
        self._anim.finished.connect(self._landed)

    def start(self) -> None:
        self.show()
        self._anim.start()

    def finish_now(self) -> None:
        """Drop the effect without handing over to a card."""
        self._on_landed = None
        self._anim.stop()
        self._finish()

    def _on_tick(self, value) -> None:
        self._t = float(value)
        self.update()

    def _landed(self) -> None:
        landed, self._on_landed = self._on_landed, None
        if landed is not None and not self._thumb.isNull():
            top_left = self._screen_geo.topLeft()
            landed(self._dst.translated(top_left.x(), top_left.y()), self._thumb)
        self._finish()

    def _finish(self) -> None:
        self.hide()
        callback, self._on_done = self._on_done, None
        if callback is not None:
            callback(self)
        self.deleteLater()

    def showEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().showEvent(event)
        hwnd = int(self.winId())
        harden_window(hwnd)
        exclude_from_capture(hwnd)

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override)
        del event
        t = self._t
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        fly = _ease_out_cubic((t - _SNAP_FLY_START_MS) / _SNAP_FLY_MS)
        rect = QRectF(
            _lerp(self._src.x(), self._dst.x(), fly),
            _lerp(self._src.y(), self._dst.y(), fly),
            _lerp(self._src.width(), self._dst.width(), fly),
            _lerp(self._src.height(), self._dst.height(), fly),
        )
        if not self._thumb.isNull():
            if fly > 0.0:
                for spread, alpha in ((10.0, 18), (5.0, 30), (2.0, 46)):
                    shadow = rect.adjusted(-spread, -spread + 4, spread, spread + 4)
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(QColor(0, 0, 0, int(alpha * fly)))
                    radius = _SNAP_RADIUS * fly + spread
                    painter.drawRoundedRect(shadow, radius, radius)
            _paint_card(painter, rect, self._thumb, _SNAP_RADIUS * fly, fly)

        flash = 1.0 - _ease_out_cubic(t / _SNAP_FLASH_MS)
        if flash > 0.0:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 255, 255, int(225 * flash)))
            painter.drawRect(self._src)
        painter.end()


#: The lifted picture under the pointer while the card is dragged: smaller
#: than the card so the drop target stays visible (CleanShot X's drag).
_DRAG_PREVIEW_W = 220.0
_DRAG_PREVIEW_H = 165.0
_DRAG_PREVIEW_RADIUS = 10.0
_DRAG_PREVIEW_PAD = 12.0  # transparent margin that holds the shadow
_DRAG_PREVIEW_OPACITY = 0.94
#: The "+" badge beside the pointer over a target that takes the picture.
_DRAG_BADGE_GREEN = QColor(52, 199, 89)


def _drag_preview(thumb: QImage, card: QRectF, grab: QPointF, dpr: float) -> tuple[QPixmap, QPoint]:
    """The lifted card for a drag, and the hotspot that keeps the grab point.

    Rounded, ringed and shadowed like the resting card, scaled down, and
    rendered at the screen's pixel ratio so it stays sharp. The hotspot is the
    spot the user pressed, mapped onto the smaller picture, so the picture
    does not jump out from under the pointer.
    """
    aspect = thumb.height() / max(1, thumb.width()) if not thumb.isNull() else 0.6
    w = _DRAG_PREVIEW_W
    h = w * aspect
    if h > _DRAG_PREVIEW_H:
        h = _DRAG_PREVIEW_H
        w = h / max(aspect, 1e-6)
    pad = _DRAG_PREVIEW_PAD
    dpr = max(1.0, float(dpr))
    size = QPointF(w + 2 * pad, h + 2 * pad)
    canvas = QPixmap(int(size.x() * dpr + 0.5), int(size.y() * dpr + 0.5))
    canvas.setDevicePixelRatio(dpr)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.setOpacity(_DRAG_PREVIEW_OPACITY)
    rect = QRectF(pad, pad, w, h)
    painter.setPen(Qt.PenStyle.NoPen)
    for spread, alpha in ((10.0, 16), (5.0, 28), (2.0, 44)):
        shadow = rect.adjusted(-spread, -spread + 4, spread, spread + 4)
        painter.setBrush(QColor(0, 0, 0, alpha))
        radius = _DRAG_PREVIEW_RADIUS + spread
        painter.drawRoundedRect(shadow, radius, radius)
    _paint_card(painter, rect, thumb, _DRAG_PREVIEW_RADIUS, 1.0)
    painter.end()
    fx = (grab.x() - card.x()) / max(1.0, card.width())
    fy = (grab.y() - card.y()) / max(1.0, card.height())
    hot = QPoint(
        int(pad + max(0.0, min(1.0, fx)) * w),
        int(pad + max(0.0, min(1.0, fy)) * h),
    )
    return canvas, hot


def _drag_copy_cursor(dpr: float) -> QPixmap:
    """An arrow with a green "+" badge — the pointer over a drop target.

    The arrow tip is the pixmap's top-left corner, where Qt puts the hotspot.
    Where the platform keeps its own drag badges (macOS), Qt ignores it.
    """
    dpr = max(1.0, float(dpr))
    canvas = QPixmap(int(30 * dpr + 0.5), int(32 * dpr + 0.5))
    canvas.setDevicePixelRatio(dpr)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    arrow = QPolygonF(
        [
            QPointF(1.0, 1.0),
            QPointF(1.0, 17.5),
            QPointF(5.0, 13.8),
            QPointF(7.8, 20.2),
            QPointF(10.6, 19.0),
            QPointF(7.9, 12.8),
            QPointF(13.0, 12.8),
        ]
    )
    pen = QPen(QColor(0, 0, 0, 230))
    pen.setWidthF(1.2)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(QColor(255, 255, 255))
    painter.drawPolygon(arrow)
    centre = QPointF(20.0, 22.0)
    ring = QPen(QColor(255, 255, 255))
    ring.setWidthF(1.6)
    painter.setPen(ring)
    painter.setBrush(_DRAG_BADGE_GREEN)
    painter.drawEllipse(centre, 8.0, 8.0)
    plus = QPen(QColor(255, 255, 255))
    plus.setWidthF(2.0)
    plus.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(plus)
    cx, cy = centre.x(), centre.y()
    painter.drawLine(QPointF(cx - 4.0, cy), QPointF(cx + 4.0, cy))
    painter.drawLine(QPointF(cx, cy - 4.0), QPointF(cx, cy + 4.0))
    painter.end()
    return canvas


def _card_rect(screen, thumb: QImage) -> QRectF:
    """Where the resting card sits: the bottom-right of ``screen``'s work area.

    Absolute (virtual-desktop) logical coordinates. Shared by the shutter
    flight's landing spot and a card that comes back after the editor.
    """
    aspect = thumb.height() / max(1, thumb.width()) if not thumb.isNull() else 0.6
    tw = float(_SNAP_THUMB_W)
    th = tw * aspect
    if th > _SNAP_THUMB_MAX_H:
        th = float(_SNAP_THUMB_MAX_H)
        tw = th / max(aspect, 1e-6)
    # Inside the work area, so the card never sits under the taskbar.
    avail = screen.availableGeometry()
    right = float(avail.right() + 1)
    bottom = float(avail.bottom() + 1)
    return QRectF(right - tw - _SNAP_MARGIN, bottom - th - _SNAP_MARGIN, tw, th)


#: Default card wording; the main process sends the user's language.
_CARD_LABELS = {
    "edit": "Edit",
    "copy": "Copy",
    "save": "Save",
    "close": "Close",
}


class _CardWindow(QWidget):
    """The resting thumbnail, CleanShot X's Quick Access Overlay in small.

    Hover shows Close (top-left), Edit (top-right) and Copy / Save in the
    middle; a click anywhere else edits, a drag shares the picture, a
    right-click dismisses it. ``rest_ms`` is how long it stays untouched —
    ``0`` keeps it until the user closes it.
    """

    _PAD = 12  # transparent margin that holds the soft shadow

    def __init__(
        self,
        rect: QRectF,
        thumb: QImage,
        hint: str,
        owner: Renderer,
        *,
        rest_ms: int = _CARD_REST_MS,
        labels: dict[str, str] | None = None,
    ) -> None:
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.Tool
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        if sys.platform == "darwin":
            mac_always_show = getattr(Qt.WidgetAttribute, "WA_MacAlwaysShowToolWindow", None)
            if mac_always_show is not None:
                self.setAttribute(mac_always_show)
        pad = self._PAD
        self.setGeometry(rect.adjusted(-pad, -pad, pad, pad).toAlignedRect())
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)
        self._thumb = thumb
        self._hint = hint
        self._owner = owner
        self._rest_ms = max(0, int(rest_ms))
        self._labels = {**_CARD_LABELS, **(labels or {})}
        self._hover = False
        self._hot = ""  # the hover button under the pointer
        self._press: QPointF | None = None
        self._press_button = ""
        self._leaving = False
        self._status = ""
        self._status_timer = QTimer(self)
        self._status_timer.setSingleShot(True)
        self._status_timer.timeout.connect(self._clear_status)
        self._dismiss = QTimer(self)
        self._dismiss.setSingleShot(True)
        self._dismiss.timeout.connect(self.leave)
        self._out = QVariantAnimation(self)
        self._out.setStartValue(0.0)
        self._out.setEndValue(1.0)
        self._out.setDuration(_CARD_OUT_MS)
        self._out.valueChanged.connect(self._on_out)
        self._out.finished.connect(self._gone)
        self._in = QVariantAnimation(self)
        self._in.setStartValue(1.0)
        self._in.setEndValue(0.0)
        self._in.setDuration(_CARD_OUT_MS)
        self._in.valueChanged.connect(self._on_out)
        self._origin = self.pos()

    def start(self, *, slide_in: bool = False) -> None:
        if slide_in:
            # Back from the editor: slide in from the edge it leaves through.
            self._origin = self.pos()
            self._on_out(1.0)
            self.show()
            self._in.start()
        else:
            self.show()
        self._arm_dismiss(self._rest_ms)

    def _arm_dismiss(self, ms: int) -> None:
        if self._rest_ms > 0 and not self._leaving:
            self._dismiss.start(ms)

    # -- lifecycle -----------------------------------------------------------
    def leave(self) -> None:
        """Slide out and go. Safe to call more than once."""
        if self._leaving:
            return
        self._leaving = True
        self._dismiss.stop()
        self._in.stop()
        self._origin = self.pos()
        self._out.start()

    def finish_now(self) -> None:
        self._leaving = True
        self._dismiss.stop()
        self._in.stop()
        self._out.stop()
        self._gone()

    def _on_out(self, value) -> None:
        t = _ease_out_cubic(float(value))
        self.move(self._origin + QPoint(int(48 * t), 0))
        self.setWindowOpacity(1.0 - t)

    def _gone(self) -> None:
        self.hide()
        owner, self._owner = self._owner, None
        if owner is not None:
            owner.card_gone(self)
        self.deleteLater()

    def show_status(self, text: str) -> None:
        """A short line over the card ("Copied", "Saved to Downloads")."""
        self._status = text
        self._status_timer.start(_CARD_STATUS_MS)
        self.update()

    def _clear_status(self) -> None:
        self._status = ""
        self.update()

    # -- input ---------------------------------------------------------------
    def showEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().showEvent(event)
        # Clickable, so no click-through hardening; layered for the
        # see-through corners, and out of every screenshot.
        hwnd = int(self.winId())
        harden_clickable_window(hwnd)
        exclude_from_capture(hwnd)

    def enterEvent(self, event) -> None:  # noqa: N802
        del event
        if self._leaving:
            return
        self._hover = True
        self._dismiss.stop()
        self.update()

    def leaveEvent(self, event) -> None:  # noqa: N802
        del event
        self._hover = False
        self._hot = ""
        self._arm_dismiss(_CARD_AFTER_HOVER_MS)
        self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.RightButton:
            self.leave()
        elif event.button() == Qt.MouseButton.LeftButton:
            self._press = event.position()
            self._press_button = self._button_at(event.position())

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        hot = self._button_at(event.position()) if self._hover else ""
        if hot != self._hot:
            self._hot = hot
            self.update()
        if self._press is None or self._leaving or self._press_button:
            return
        moved = event.position() - self._press
        if abs(moved.x()) + abs(moved.y()) > _CARD_DRAG_SLOP:
            grab, self._press = self._press, None
            self._start_drag(grab)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton or self._press is None:
            return
        self._press = None
        pressed, self._press_button = self._press_button, ""
        if self._owner is None or self._leaving:
            return
        released = self._button_at(event.position())
        if pressed and pressed != released:
            return  # pressed a button, let go elsewhere: nothing
        if pressed == "close":
            self.leave()
        elif pressed in ("copy", "save"):
            if self._owner.card_image is not None:
                self._owner.card_action(pressed)
        else:  # "edit" or the picture itself
            self._owner.card_clicked(self)

    def _start_drag(self, grab: QPointF) -> None:
        owner = self._owner
        image = owner.card_image if owner is not None else None
        if owner is None or image is None or image.isNull():
            # Only the finished, privacy-filtered picture may leave this
            # process; the thumbnail is cut from the raw frame.
            return
        path = owner.write_drag_file(image)
        if path is None:
            return
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(path))])
        mime.setImageData(image)
        drag = QDrag(self)
        drag.setMimeData(mime)
        # CleanShot X's drag: the card lifts off as a smaller, rounded copy
        # held where it was grabbed, and a green "+" beside the pointer says
        # the field or window under it takes the picture.
        dpr = float(self.devicePixelRatioF() or 1.0)
        preview, hot = _drag_preview(self._thumb, self._card_area(), grab, dpr)
        drag.setPixmap(preview)
        drag.setHotSpot(hot)
        drag.setDragCursor(_drag_copy_cursor(dpr), Qt.DropAction.CopyAction)
        self._dismiss.stop()
        self.setWindowOpacity(0.35)
        result = drag.exec(Qt.DropAction.CopyAction)
        self.setWindowOpacity(1.0)
        if result == Qt.DropAction.IgnoreAction:
            # Dropped nowhere (or Esc): the card stays, like CleanShot's.
            self._hover = False
            self._hot = ""
            self._arm_dismiss(_CARD_AFTER_HOVER_MS)
            self.update()
            return
        self.leave()

    # -- hover buttons -------------------------------------------------------
    def _card_area(self) -> QRectF:
        pad = float(self._PAD)
        return QRectF(pad, pad, self.width() - 2 * pad, self.height() - 2 * pad)

    def _buttons(self) -> dict[str, QRectF]:
        """Hit areas of the hover buttons, in widget coordinates."""
        rect = self._card_area()
        font = _card_font()
        metrics = QFontMetricsF(font)
        h = _CARD_BUTTON_H
        buttons: dict[str, QRectF] = {
            "close": QRectF(rect.left() + 8, rect.top() + 8, _CARD_ROUND, _CARD_ROUND),
        }
        edit_w = metrics.horizontalAdvance(self._labels["edit"]) + 22.0
        buttons["edit"] = QRectF(rect.right() - 8 - edit_w, rect.top() + 8, edit_w, _CARD_ROUND)
        if self._owner is not None and self._owner.card_image is not None:
            widths = [metrics.horizontalAdvance(self._labels[k]) + 28.0 for k in ("copy", "save")]
            width = max(widths + [86.0])
            gap = 8.0
            if width * 2 + gap <= rect.width() - 24:
                y = rect.center().y() - h / 2
                left = rect.center().x() - width - gap / 2
                buttons["copy"] = QRectF(left, y, width, h)
                buttons["save"] = QRectF(left + width + gap, y, width, h)
            else:
                top = rect.center().y() - h - gap / 2
                left = rect.center().x() - width / 2
                buttons["copy"] = QRectF(left, top, width, h)
                buttons["save"] = QRectF(left, top + h + gap, width, h)
        return buttons

    def _button_at(self, pos: QPointF) -> str:
        if not self._hover:
            return ""
        for name, area in self._buttons().items():
            if area.adjusted(-2, -2, 2, 2).contains(pos):
                return name
        return ""

    # -- painting ------------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override)
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        rect = self._card_area()
        painter.setPen(Qt.PenStyle.NoPen)
        for spread, alpha in ((8.0, 18), (4.0, 30), (1.5, 46)):
            painter.setBrush(QColor(0, 0, 0, alpha))
            shadow = rect.adjusted(-spread, -spread + 3, spread, spread + 3)
            painter.drawRoundedRect(shadow, _SNAP_RADIUS + spread, _SNAP_RADIUS + spread)
        _paint_card(painter, rect, self._thumb, _SNAP_RADIUS, 1.0)
        if self._status:
            self._paint_scrim(painter, rect, 150)
            self._paint_centered(painter, rect, self._status)
        elif self._hover:
            self._paint_scrim(painter, rect, 105)
            self._paint_buttons(painter)
            if self._hint:
                self._paint_hint(painter, rect)
        painter.end()

    def _paint_scrim(self, painter: QPainter, rect: QRectF, alpha: int) -> None:
        clip = QPainterPath()
        clip.addRoundedRect(rect, _SNAP_RADIUS, _SNAP_RADIUS)
        painter.save()
        painter.setClipPath(clip)
        painter.fillRect(rect, QColor(0, 0, 0, alpha))
        painter.restore()

    def _paint_buttons(self, painter: QPainter) -> None:
        font = _card_font()
        painter.setFont(font)
        for name, area in self._buttons().items():
            hot = name == self._hot
            painter.setPen(Qt.PenStyle.NoPen)
            if name in ("copy", "save"):
                # Light pills on the dimmed picture, like CleanShot's overlay.
                painter.setBrush(QColor(255, 255, 255, 255 if hot else 228))
                painter.drawRoundedRect(area, area.height() / 2, area.height() / 2)
                painter.setPen(QColor(20, 20, 22))
                painter.drawText(area, Qt.AlignmentFlag.AlignCenter, self._labels[name])
                continue
            painter.setBrush(QColor(28, 28, 30, 240 if hot else 200))
            painter.drawRoundedRect(area, area.height() / 2, area.height() / 2)
            if name == "close":
                pen = QPen(QColor(255, 255, 255, 235))
                pen.setWidthF(1.6)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                painter.setPen(pen)
                c = area.center()
                d = area.width() * 0.2
                painter.drawLine(QPointF(c.x() - d, c.y() - d), QPointF(c.x() + d, c.y() + d))
                painter.drawLine(QPointF(c.x() - d, c.y() + d), QPointF(c.x() + d, c.y() - d))
            else:
                painter.setPen(QColor(255, 255, 255, 240))
                painter.drawText(area, Qt.AlignmentFlag.AlignCenter, self._labels[name])

    def _paint_centered(self, painter: QPainter, rect: QRectF, text: str) -> None:
        font = _card_font()
        font.setPointSizeF(10.0)
        painter.setFont(font)
        painter.setPen(QColor(255, 255, 255, 245))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, text)

    def _paint_hint(self, painter: QPainter, rect: QRectF) -> None:
        font = QFont()
        font.setPointSizeF(8.5)
        font.setWeight(QFont.Weight.Medium)
        metrics = QFontMetricsF(font)
        h = metrics.height() + 10.0
        band = QRectF(rect.x(), rect.bottom() - h, rect.width(), h)
        painter.setFont(font)
        painter.setPen(QColor(255, 255, 255, 200))
        painter.drawText(band, Qt.AlignmentFlag.AlignCenter, self._hint)


def _card_font() -> QFont:
    font = QFont()
    font.setPointSizeF(9.0)
    font.setWeight(QFont.Weight.DemiBold)
    return font


class Renderer(QObject):
    """Owns the per-monitor windows, the animations, and the IPC slots."""

    def __init__(self, app: QApplication) -> None:
        super().__init__()
        self._app = app
        self._windows: list[_GlowWindow] = []
        self._snaps: list[_SnapWindow] = []
        self._card: _CardWindow | None = None
        self._card_hint = ""
        self._card_rest_ms = _CARD_REST_MS
        self._card_labels: dict[str, str] = {}
        #: The finished (redacted) picture a drag from the card hands out.
        self.card_image: QImage | None = None
        self._hint = ""
        self._active = False  # "show" was requested and not yet "hide"
        self._blanked = False  # capture guard currently hiding the border
        self._commands: deque[dict] = deque()
        self._processing_commands = False

        # Breathing pulse: 0 → 1 → 0 per period, applied as a factor on
        # top of the master fade so show/hide and breathing compose.
        self._pulse_value = 1.0
        self._pulse = QVariantAnimation(self)
        self._pulse.setStartValue(0.0)
        self._pulse.setEndValue(0.0)
        self._pulse.setKeyValueAt(0.5, 1.0)
        self._pulse.setDuration(_PULSE_PERIOD_MS)
        self._pulse.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._pulse.setLoopCount(-1)
        self._pulse.valueChanged.connect(self._on_pulse)

        # Master fade 0..1 (show/hide).
        self._master_value = 0.0
        self._master = QVariantAnimation(self)
        self._master.setDuration(_FADE_MS)
        self._master.valueChanged.connect(self._on_master)
        self._master.finished.connect(self._on_master_done)

    # -- IPC entry (runs on the Qt main thread via queued signal) ---------
    @Slot(str)
    def on_line(self, raw: str) -> None:
        payload = protocol.decode_command(raw)
        if payload is None:
            return
        self._commands.append(payload)
        if self._processing_commands:
            return
        self._drain_commands()

    def _drain_commands(self) -> None:
        """Apply and acknowledge commands in wire order.

        ``processEvents()`` is needed to flush native show/hide work before an
        acknowledgement, but it may re-enter :meth:`on_line`. Keeping a local
        FIFO and one active drain prevents nested calls from acknowledging a
        later command first.
        """
        self._processing_commands = True
        try:
            while self._commands:
                payload = self._commands.popleft()
                cmd = payload["cmd"]
                if cmd == protocol.CMD_SHOW:
                    self._show(str(payload.get("hint", "")))
                elif cmd == protocol.CMD_HIDE:
                    self._hide()
                elif cmd == protocol.CMD_BLANK:
                    self._blank()
                elif cmd == protocol.CMD_UNBLANK:
                    self._unblank()
                elif cmd == protocol.CMD_SNAP:
                    self._snap(payload)
                elif cmd == protocol.CMD_SNAP_IMAGE:
                    self._snap_image(payload)
                elif cmd == protocol.CMD_CARD:
                    self._card_cmd(payload)
                elif cmd == protocol.CMD_CARD_STATUS:
                    if self._card is not None:
                        self._card.show_status(str(payload.get("text", "")))
                elif cmd == protocol.CMD_QUIT:
                    _ack(cmd)
                    self._app.quit()
                    self._commands.clear()
                    return
                # The controller treats SHOW as a privacy boundary: flush the
                # native window-system work before reporting it as visible.
                self._app.processEvents()
                _ack(cmd)
        finally:
            self._processing_commands = False

    # -- commands ----------------------------------------------------------
    def _show(self, hint: str) -> None:
        self._hint = hint
        self._active = True
        self._blanked = False
        self._ensure_windows()
        for win in self._windows:
            win.set_hint(hint)
            win.show()
        # A fade starting at zero can ACK while still fully transparent. Put a
        # small visible floor on screen synchronously, then continue the fade.
        self._master_value = max(self._master_value, 0.25)
        self._apply_opacity()
        self._pulse.start()
        self._fade_to(1.0)

    def _hide(self) -> None:
        self._active = False
        self._fade_to(0.0)

    def _snap(self, payload: dict) -> None:
        thumb = QImage()
        raw = payload.get("thumb")
        if isinstance(raw, str) and raw:
            thumb.loadFromData(QByteArray.fromBase64(raw.encode("ascii")))
        rect = payload.get("rect")
        if not isinstance(rect, list) or len(rect) != 4:
            rect = [0.0, 0.0, 1.0, 1.0]
        monitor = payload.get("monitor")
        screen = _match_screen(monitor if isinstance(monitor, list) else [])
        if screen is None:
            return
        # One effect at a time: a second appshot replaces the resting card.
        for old in list(self._snaps):
            old.finish_now()
        if self._card is not None:
            self._card.finish_now()
        self.card_image = None
        self._card_hint = str(payload.get("hint", "") or "")
        self._take_card_options(payload)
        win = _SnapWindow(screen, rect, thumb, self._snap_landed, self._snap_done)
        self._snaps.append(win)
        win.start()

    def _snap_done(self, win) -> None:
        with suppress(ValueError):
            self._snaps.remove(win)

    def _take_card_options(self, payload: dict) -> None:
        try:
            self._card_rest_ms = max(0, int(payload.get("rest_ms", _CARD_REST_MS)))
        except (TypeError, ValueError):
            self._card_rest_ms = _CARD_REST_MS
        labels = payload.get("labels")
        self._card_labels = (
            {str(k): str(v) for k, v in labels.items()} if isinstance(labels, dict) else {}
        )

    def _new_card(self, rect: QRectF, thumb: QImage) -> _CardWindow:
        card = _CardWindow(
            rect,
            thumb,
            self._card_hint,
            self,
            rest_ms=self._card_rest_ms,
            labels=self._card_labels,
        )
        self._card = card
        _emit(protocol.EVENT_CARD, open=True)
        return card

    def _snap_landed(self, rect: QRectF, thumb: QImage) -> None:
        self._new_card(rect, thumb).start()

    def _card_cmd(self, payload: dict) -> None:
        """The editor closed: slide the (edited) picture back into the corner."""
        thumb = QImage()
        raw = payload.get("thumb")
        if isinstance(raw, str) and raw:
            thumb.loadFromData(QByteArray.fromBase64(raw.encode("ascii")))
        if thumb.isNull():
            return
        monitor = payload.get("monitor")
        screen = _match_screen(monitor if isinstance(monitor, list) else [])
        if screen is None:
            return
        for old in list(self._snaps):
            old.finish_now()
        if self._card is not None:
            self._card.finish_now()
        self.card_image = None
        self._card_hint = str(payload.get("hint", "") or "")
        self._take_card_options(payload)
        self._new_card(_card_rect(screen, thumb), thumb).start(slide_in=True)

    def card_action(self, action: str) -> None:
        """A hover button: the main process copies or saves the held appshot."""
        _emit(protocol.EVENT_CARD_ACTION, action=action)

    def _snap_image(self, payload: dict) -> None:
        raw = payload.get("image")
        if not isinstance(raw, str) or not raw:
            return
        image = QImage()
        if image.loadFromData(QByteArray.fromBase64(raw.encode("ascii"))):
            self.card_image = image

    def card_gone(self, card: _CardWindow) -> None:
        if self._card is card:
            self._card = None
            self.card_image = None
            _emit(protocol.EVENT_CARD, open=False)

    def card_clicked(self, card: _CardWindow) -> None:
        _emit(protocol.EVENT_SNAP_OPEN)
        card.leave()

    @staticmethod
    def write_drag_file(image: QImage) -> Path | None:
        """Write the picture for a drag — the one moment it touches disk.

        Only on the user's drag; files older than an hour are removed first,
        so the folder never grows (see ``docs/appshots.md``).
        """
        folder = Path(tempfile.gettempdir()) / "jarvis-appshots"
        try:
            folder.mkdir(parents=True, exist_ok=True)
            cutoff = time.time() - _DRAG_FILE_MAX_AGE_S
            for old in folder.glob("appshot-*.png"):
                with suppress(OSError):  # a file still open elsewhere stays
                    if old.stat().st_mtime < cutoff:
                        old.unlink()
            path = folder / time.strftime("appshot-%Y%m%d-%H%M%S.png")
            if not image.save(str(path), "PNG"):
                return None
            return path
        except OSError as exc:
            sys.stderr.write(f"cu-indicator: drag file failed ({exc!r})\n")
            return None

    def _blank(self) -> None:
        # A resting thumbnail must never end up inside the next capture.
        for snap in list(self._snaps):
            snap.finish_now()
        if self._card is not None:
            self._card.hide()
        if not self._active:
            return
        self._blanked = True
        for win in self._windows:
            win.hide()

    def _unblank(self) -> None:
        if self._card is not None and not self._card.isVisible():
            self._card.show()
        if not self._active or not self._blanked:
            return
        self._blanked = False
        for win in self._windows:
            win.show()

    # -- screens -----------------------------------------------------------
    def _ensure_windows(self) -> None:
        for win in self._windows:
            win.close()
            win.deleteLater()
        primary = QGuiApplication.primaryScreen()
        self._windows = [
            _GlowWindow(screen, with_pill=screen is primary, hint=self._hint)
            for screen in QGuiApplication.screens()
        ]
        self._apply_opacity()

    def on_screens_changed(self, *_args) -> None:
        """Monitor hotplug while visible → rebuild windows in place."""
        if self._active:
            self._ensure_windows()
            if not self._blanked:
                for win in self._windows:
                    win.show()

    # -- animation plumbing --------------------------------------------------
    def _fade_to(self, target: float) -> None:
        self._master.stop()
        self._master.setStartValue(self._master_value)
        self._master.setEndValue(target)
        self._master.start()

    def _on_pulse(self, value) -> None:
        self._pulse_value = float(value)
        self._apply_opacity()

    def _on_master(self, value) -> None:
        self._master_value = float(value)
        self._apply_opacity()

    def _on_master_done(self) -> None:
        if self._master_value <= 0.0 and not self._active:
            self._pulse.stop()
            for win in self._windows:
                win.hide()

    def _apply_opacity(self) -> None:
        breathing = _PULSE_FLOOR + (1.0 - _PULSE_FLOOR) * self._pulse_value
        opacity = max(0.0, min(1.0, self._master_value * breathing))
        for win in self._windows:
            win.setWindowOpacity(opacity)


class _StdinPump(QObject):
    """Reads stdin on a daemon thread; signals deliver to the Qt thread."""

    line = Signal(str)
    eof = Signal()

    def start(self) -> None:
        threading.Thread(target=self._run, name="cu-indicator-stdin", daemon=True).start()

    def _run(self) -> None:
        # A dying pipe simply means "parent gone" — treated as EOF.
        with suppress(Exception):
            for raw in sys.stdin:
                self.line.emit(raw)
        self.eof.emit()


def _emit(event: str, **fields) -> None:
    # A failed write means the parent is gone; the EOF path quits the app.
    with suppress(Exception):
        sys.stdout.write(protocol.encode_event(event, **fields))
        sys.stdout.flush()


def _ack(cmd: str) -> None:
    # A failed ack means the parent is gone; the EOF path quits the app.
    with suppress(Exception):
        sys.stdout.write(protocol.encode_ack(cmd))
        sys.stdout.flush()


def run() -> int:
    """Sidecar main loop. Returns the process exit code."""
    # Per-monitor DPI: hand Qt the real per-screen scale factors so the
    # glow hugs the true monitor edges on mixed-DPI setups.
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    try:
        app = QApplication(sys.argv[:1] or ["cu-indicator"])
    except Exception as exc:  # noqa: BLE001 — no display / no platform plugin
        sys.stderr.write(f"cu-indicator: no usable display ({exc!r}) — indicator disabled.\n")
        return protocol.EXIT_NO_GUI
    # All windows are frequently hidden (blank/hide) — that must never
    # terminate the sidecar; only stdin EOF or "quit" does.
    app.setQuitOnLastWindowClosed(False)

    renderer = Renderer(app)
    QGuiApplication.instance().screenAdded.connect(renderer.on_screens_changed)
    QGuiApplication.instance().screenRemoved.connect(renderer.on_screens_changed)

    pump = _StdinPump()
    pump.line.connect(renderer.on_line, Qt.ConnectionType.QueuedConnection)
    pump.eof.connect(app.quit, Qt.ConnectionType.QueuedConnection)
    pump.start()

    if os.environ.get("JARVIS_CU_INDICATOR_AUTOSHOW"):
        # Debug/verification convenience: show immediately without a parent.
        renderer.on_line(
            protocol.encode_command(
                protocol.CMD_SHOW,
                hint=os.environ.get("JARVIS_CU_INDICATOR_HINT", "Esc to cancel"),
            )
        )

    return app.exec()


__all__ = ["Renderer", "run"]
