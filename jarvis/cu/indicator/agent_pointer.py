"""The look of Jarvis operating the screen: agent pointer, border and hint pill.

Sidecar-only (imports PySide6 at module level, like ``renderer.py``).

Visual contract (2026-10-03):

- **Border.** A thin blue line along every monitor edge with softly rounded
  inner corners, over a narrow inner glow. It breathes gently through the
  renderer's window opacity; nothing is repainted per frame.
- **Agent pointer.** While Jarvis has the mouse and keyboard, the pointer
  becomes a rounded arrow with a blue gradient body, a white rim, a soft
  shadow and a blue halo. It glides between targets on a bowed path, leans
  into its motion, dips and rings on every click
  (:mod:`jarvis.cu.indicator.pointer_motion`). On Windows the system pointer
  is hidden meanwhile and comes back the moment control ends; macOS and
  Linux keep the system pointer under it.
- **Hint pill.** A dark glass pill with a blue dot: "Esc to cancel".

Both windows are click-through, never activate, and are excluded from screen
capture where the OS allows it (the model never sees its own pointer).
"""

from __future__ import annotations

import sys
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QFontMetricsF,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)
from PySide6.QtWidgets import QWidget

from jarvis.cu.indicator.pointer_motion import PointerMotion, PointerPose
from jarvis.cu.indicator.win32 import exclude_from_capture, harden_window

#: The product accent (``--accent`` dark, ``#3D8BFF``) and its light sibling.
ACCENT = (61, 139, 255)
ACCENT_LIGHT = (126, 182, 255)
ACCENT_DEEP = (36, 99, 235)

_BORDER_LINE_PX = 2.0
_BORDER_CORNER_PX = 14.0
_BORDER_GLOW_MIN_PX = 16
_BORDER_GLOW_MAX_PX = 34

#: Pointer window: the arrow tip sits at ``_HOTSPOT`` inside a square canvas
#: large enough for the halo and a full click ring around the tip.
_CANVAS_PX = 128
_HOTSPOT = QPointF(46.0, 46.0)
_FRAME_MS = 16
_RING_MAX_PX = 30.0
_HALO_PX = 34.0

# A slim arrowhead, tip at the origin, its axis 60 degrees below the
# horizontal (up-left like a pointer), with a notched base. Logical px; the
# round-joined rim softens every corner.
_ARROW = (
    (0.0, 0.0),
    (4.3, 26.6),
    (9.0, 15.6),
    (20.9, 17.0),
)
#: Where the halo sits: the arrowhead's visual center.
_BODY_CENTER = QPointF(9.1, 14.9)


def _arrow_path() -> QPainterPath:
    path = QPainterPath()
    path.moveTo(*_ARROW[0])
    for point in _ARROW[1:]:
        path.lineTo(*point)
    path.closeSubpath()
    return path


def _ui_font(point_size: float, weight: QFont.Weight) -> QFont:
    font = QFont("Inter")
    font.setStyleHint(QFont.StyleHint.SansSerif)
    font.setPointSizeF(point_size)
    font.setWeight(weight)
    return font


def paint_border(painter: QPainter, width: int, height: int) -> None:
    """The control border on one monitor: inner glow plus a crisp rounded line."""
    glow = max(_BORDER_GLOW_MIN_PX, min(_BORDER_GLOW_MAX_PX, int(min(width, height) * 0.022)))
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    inner = QColor(*ACCENT, 92)
    mid = QColor(*ACCENT, 26)
    clear = QColor(*ACCENT, 0)

    def edge(x1: float, y1: float, x2: float, y2: float) -> QLinearGradient:
        gradient = QLinearGradient(x1, y1, x2, y2)
        gradient.setColorAt(0.0, inner)
        gradient.setColorAt(0.4, mid)
        gradient.setColorAt(1.0, clear)
        return gradient

    painter.fillRect(QRectF(0, 0, width, glow), edge(0, 0, 0, glow))
    painter.fillRect(QRectF(0, height - glow, width, glow), edge(0, height, 0, height - glow))
    painter.fillRect(QRectF(0, 0, glow, height), edge(0, 0, glow, 0))
    painter.fillRect(QRectF(width - glow, 0, glow, height), edge(width, 0, width - glow, 0))

    inset = _BORDER_LINE_PX / 2
    rect = QRectF(inset, inset, width - _BORDER_LINE_PX, height - _BORDER_LINE_PX)
    # A faint wide stroke under the crisp one reads as light, not as a frame.
    for pen_width, alpha in ((_BORDER_LINE_PX + 5.0, 48), (_BORDER_LINE_PX, 235)):
        pen = QPen(QColor(*ACCENT_LIGHT if alpha > 100 else ACCENT, alpha), pen_width)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect, _BORDER_CORNER_PX, _BORDER_CORNER_PX)


def paint_hint_pill(painter: QPainter, width: int, hint: str) -> None:
    """The "Esc to cancel" pill, top-center."""
    font = _ui_font(10.0, QFont.Weight.Medium)
    painter.setFont(font)
    metrics = QFontMetricsF(font)
    dot = 7.0
    pad_x, pad_y, gap = 14.0, 7.0, 8.0
    text_w = metrics.horizontalAdvance(hint)
    pill_w = pad_x + dot + gap + text_w + pad_x
    pill_h = metrics.height() + 2 * pad_y
    pill = QRectF((width - pill_w) / 2.0, 16.0, pill_w, pill_h)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor(*ACCENT, 110), 1.0))
    painter.setBrush(QColor(10, 10, 12, 214))
    painter.drawRoundedRect(pill, pill_h / 2.0, pill_h / 2.0)
    center_y = pill.center().y()
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(*ACCENT, 70))
    painter.drawEllipse(QPointF(pill.left() + pad_x + dot / 2, center_y), dot, dot)
    painter.setBrush(QColor(*ACCENT_LIGHT, 255))
    painter.drawEllipse(QPointF(pill.left() + pad_x + dot / 2, center_y), dot / 2, dot / 2)
    painter.setPen(QColor(250, 250, 250, 235))
    painter.drawText(
        QRectF(pill.left() + pad_x + dot + gap, pill.top(), text_w + 1, pill_h),
        Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
        hint,
    )


def paint_pointer(painter: QPainter, origin: QPointF, pose: PointerPose) -> None:
    """Rings, halo, shadow and the arrow, tip at ``origin``."""
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    for progress in pose.rings:
        radius = 6.0 + (_RING_MAX_PX - 6.0) * (1 - (1 - progress) ** 3)
        alpha = int(200 * (1 - progress))
        painter.setPen(QPen(QColor(*ACCENT_LIGHT, alpha), 2.0))
        painter.setBrush(QColor(*ACCENT, int(alpha * 0.18)))
        painter.drawEllipse(origin, radius, radius)

    painter.save()
    painter.translate(origin)
    painter.rotate(pose.angle)
    painter.scale(pose.scale, pose.scale)

    halo = QRadialGradient(_BODY_CENTER, _HALO_PX)
    halo.setColorAt(0.0, QColor(*ACCENT, 92))
    halo.setColorAt(0.45, QColor(*ACCENT, 34))
    halo.setColorAt(1.0, QColor(*ACCENT, 0))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(halo)
    painter.drawEllipse(_BODY_CENTER, _HALO_PX, _HALO_PX)

    arrow = _arrow_path()
    # Soft shadow: widening strokes of falling opacity read as a blur.
    shadow = arrow.translated(0.8, 1.8)
    for width, alpha in ((8.0, 8), (5.5, 12), (3.0, 18)):
        pen = QPen(QColor(0, 6, 24, alpha), width)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(QColor(0, 6, 24, alpha))
        painter.drawPath(shadow)

    body = QLinearGradient(0.0, 0.0, 14.0, 24.0)
    body.setColorAt(0.0, QColor(*ACCENT_LIGHT))
    body.setColorAt(0.55, QColor(*ACCENT))
    body.setColorAt(1.0, QColor(*ACCENT_DEEP))
    rim = QPen(QColor(255, 255, 255, 250), 2.2)
    rim.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(rim)
    painter.setBrush(body)
    painter.drawPath(arrow)
    # A thin highlight along the left flank gives the body some depth.
    painter.setPen(QPen(QColor(255, 255, 255, 96), 1.0))
    painter.drawLine(QPointF(1.8, 4.8), QPointF(4.3, 19.9))
    painter.restore()


class AgentPointerWindow(QWidget):
    """A small click-through canvas that follows the real cursor."""

    def __init__(self) -> None:
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
        self.resize(_CANVAS_PX, _CANVAS_PX)
        self._motion = PointerMotion()
        self._pose: PointerPose | None = None
        self._running = False
        self._blanked = False
        self._timer = QTimer(self)
        self._timer.setInterval(_FRAME_MS)
        self._timer.timeout.connect(self._tick)

    # -- lifecycle -------------------------------------------------------
    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._motion.reset()
        self._tick()
        self._timer.start()
        if not self._blanked:
            self.show()

    def stop(self) -> None:
        self._running = False
        self._timer.stop()
        self.hide()

    def press(self) -> None:
        if self._running:
            self._motion.press(time.monotonic())

    def set_blanked(self, blanked: bool) -> None:
        """Hide for a frame grab on hosts without capture exclusion."""
        self._blanked = blanked
        if blanked:
            self.hide()
        elif self._running:
            self.show()

    # -- frame loop ------------------------------------------------------
    def _tick(self) -> None:
        cursor = QCursor.pos()
        pose = self._motion.step((float(cursor.x()), float(cursor.y())), time.monotonic())
        self._pose = pose
        self.move(round(pose.x - _HOTSPOT.x()), round(pose.y - _HOTSPOT.y()))
        self.update()

    def showEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().showEvent(event)
        hwnd = int(self.winId())
        harden_window(hwnd)
        exclude_from_capture(hwnd)

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override)
        del event
        if self._pose is None:
            return
        painter = QPainter(self)
        paint_pointer(painter, _HOTSPOT, self._pose)
        painter.end()


__all__ = [
    "ACCENT",
    "AgentPointerWindow",
    "paint_border",
    "paint_hint_pill",
    "paint_pointer",
]
