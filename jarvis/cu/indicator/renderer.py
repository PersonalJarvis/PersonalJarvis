"""PySide6 renderer for the Computer-Use screen indicator.

Runs ONLY inside the sidecar process (``python -m jarvis.cu.indicator``).
This module imports PySide6 at module level — the main Jarvis process must
never import it (boot-path discipline, AP-26); the ``__main__`` entry
guards the import and exits with ``protocol.EXIT_NO_GUI`` when the GUI
stack is unavailable.

Visual contract (2026-10-03, drawn by ``agent_pointer.py``):

- A thin blue line with a narrow inner glow along every edge of EVERY
  monitor, breathing on a ~3.2 s sine loop, 300 ms fade in/out.
- An "Esc to cancel" pill top-center on the primary monitor (text arrives
  pre-localized from the controller; omitted when Escape isn't armable).
- While a show carries ``pointer``: the agent pointer follows the cursor,
  and on Windows the system pointer is hidden until the hide.
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
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
)
from PySide6.QtWidgets import QApplication, QWidget

from jarvis.cu.indicator import protocol
from jarvis.cu.indicator.agent_pointer import (
    AgentPointerWindow,
    paint_border,
    paint_hint_pill,
)
from jarvis.cu.indicator.win32 import (
    exclude_from_capture,
    harden_clickable_window,
    harden_window,
    hide_system_cursor,
    restore_system_cursor,
)

_PULSE_PERIOD_MS = 3200
_PULSE_FLOOR = 0.78  # breathing dims to 78 %, calm and never out
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
        painter = QPainter(self)
        paint_border(painter, w, h)
        if self._with_pill and self._hint:
            paint_hint_pill(painter, w, self._hint)
        painter.end()


# ---------------------------------------------------------------------------
# Appshot shutter effect — the phone-screenshot moment.
#
# A white flash over the captured surface, then the picture shrinks into the
# bottom-right corner of that monitor. There it becomes a small interactive
# card (``_CardWindow``): hovering keeps it, a click asks the app to open the
# editor, a drag hands the finished picture to any app that takes a file, a
# right-click dismisses it. The flight canvas itself stays click-through.
#
# Cards stack vertically: the newest always lands at the bottom and
# the older ones glide up to make room; when one goes, the ones above it
# glide down. At most ``protocol.MAX_CARDS`` stay, the oldest leaves first.
# ---------------------------------------------------------------------------

_SNAP_FLASH_MS = 200
_SNAP_FLY_START_MS = 90
_SNAP_FLY_MS = 430
_SNAP_TOTAL_MS = _SNAP_FLY_START_MS + _SNAP_FLY_MS
_SNAP_THUMB_W = 320
_SNAP_THUMB_MAX_H = 240
#: The card never gets smaller than this, however thin or tiny the capture:
#: a 1280x18 strip still lands as a card with room for its buttons. The
#: picture sits centred on a neutral backdrop and is only ever scaled down.
_CARD_MIN_W = 200.0
_CARD_MIN_H = 112.0
_CARD_BACKDROP = QColor(28, 28, 32, 242)
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
#: Hover buttons: the stacked Copy / Save pills and the white corner chips.
_CARD_BUTTON_H = 30.0
_CARD_ROUND = 28.0
_CARD_INSET = 8.0
#: The corner stack: the gap between two cards and how long a card glides.
_CARD_STACK_GAP = 12.0
_CARD_MOVE_MS = 220
#: Drag files older than this are removed the next time one is written.
_DRAG_FILE_MAX_AGE_S = 3600


def _ease_out_cubic(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return 1.0 - (1.0 - x) ** 3


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _flight_origin(raw: object):
    """``(screen, rect as fractions of it)`` for a flight start, or ``None``.

    ``raw`` is ``[x, y, w, h]`` in global logical pixels — where the editor
    showed the picture. Anything unusable means "no flight".
    """
    if not isinstance(raw, list) or len(raw) != 4:
        return None
    try:
        x, y, w, h = (float(v) for v in raw)
    except (TypeError, ValueError):
        return None
    if w < 8 or h < 8:
        return None
    centre = QPointF(x + w / 2.0, y + h / 2.0).toPoint()
    screen = QGuiApplication.screenAt(centre) or QGuiApplication.primaryScreen()
    if screen is None:
        return None
    g = screen.geometry()
    gw, gh = max(1.0, float(g.width())), max(1.0, float(g.height()))
    return screen, [(x - g.x()) / gw, (y - g.y()) / gh, w / gw, h / gh]


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


def _screen_named(name: str):
    """The screen a card sits on, by name; primary when it went away."""
    for screen in QGuiApplication.screens():
        if screen.name() == name:
            return screen
    return QGuiApplication.primaryScreen()


def _card_geometry(
    width: float,
    height: float,
    *,
    max_w: float = _SNAP_THUMB_W,
    max_h: float = _SNAP_THUMB_MAX_H,
    min_w: float | None = None,
    min_h: float | None = None,
) -> tuple[float, float, float, float]:
    """``(card w, card h, picture w, picture h)`` for a picture of ``width`` x ``height``.

    The picture keeps its aspect and is scaled down to fit, never up: a tiny
    capture shows at its real size. The card around it is at least
    ``min_w`` x ``min_h``, so a sliver or a few pixels never vanish.
    """
    min_w = _CARD_MIN_W if min_w is None else min_w
    min_h = _CARD_MIN_H if min_h is None else min_h
    width, height = max(1.0, float(width)), max(1.0, float(height))
    scale = min(1.0, max_w / width, max_h / height)
    pw, ph = width * scale, height * scale
    return min(max_w, max(pw, min_w)), min(max_h, max(ph, min_h)), pw, ph


def _centred(box: QRectF, width: float, height: float) -> QRectF:
    return QRectF(
        box.center().x() - width / 2.0, box.center().y() - height / 2.0, width, height
    )


def _paint_card(
    painter: QPainter,
    rect: QRectF,
    thumb: QImage,
    radius: float,
    ring: float,
    picture: QRectF | None = None,
) -> None:
    """The thumbnail with rounded corners and a white ring — flight and card.

    ``picture`` is where the image goes inside ``rect``; by default it fills
    ``rect`` as far as its aspect allows. Whatever it leaves free shows the
    neutral backdrop instead of a stretched image.
    """
    if picture is None and not thumb.isNull():
        width, height = float(thumb.width()), float(thumb.height())
        scale = min(rect.width() / width, rect.height() / height)
        picture = _centred(rect, width * scale, height * scale)
    clip = QPainterPath()
    clip.addRoundedRect(rect, radius, radius)
    painter.save()
    painter.setClipPath(clip)
    framed = picture is not None and (
        picture.width() < rect.width() - 0.75 or picture.height() < rect.height() - 0.75
    )
    if framed:
        painter.fillRect(rect, _CARD_BACKDROP)
    painter.drawImage(picture if picture is not None else rect, thumb)
    if framed:
        # A hairline marks where a small picture ends on the backdrop.
        edge = QPen(QColor(255, 255, 255, 56))
        edge.setWidthF(1.0)
        painter.setPen(edge)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(picture.adjusted(-0.5, -0.5, 0.5, 0.5))
    painter.restore()
    if ring > 0.0:
        pen = QPen(QColor(255, 255, 255, int(235 * ring)))
        pen.setWidthF(2.5)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect, radius, radius)


class _SnapWindow(QWidget):
    """One monitor-sized, click-through canvas for the flash and the flight."""

    def __init__(
        self,
        screen,
        rect_frac: list[float],
        thumb: QImage,
        on_landed,
        on_done,
        *,
        flash: bool = True,
    ) -> None:
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
        #: Which appshot is flying and its finished picture (``snap_image``),
        #: handed to the card it becomes.
        self.appshot_id = ""
        self.image: QImage | None = None
        # No flash when the picture is not a fresh capture (back from the
        # editor): only the flight into the corner.
        self._flash = flash
        w, h = float(screen.geometry().width()), float(screen.geometry().height())
        fx, fy, fw, fh = (max(0.0, min(1.0, float(v))) for v in rect_frac)
        self._src = QRectF(fx * w, fy * h, max(1.0, fw * w), max(1.0, fh * h))
        # The captured area is the picture's real size on screen.
        tw, th, pw, ph = _card_geometry(self._src.width(), self._src.height())
        #: The picture inside the landed card (the rest is backdrop).
        self.picture_size = (pw, ph)
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

    def land_now(self) -> None:
        """Skip the rest of the flight: the card appears in the corner at once."""
        self._anim.stop()
        self._landed()

    @property
    def landing_height(self) -> float:
        """How tall the card it becomes is — the slot the stack keeps free."""
        return float(self._dst.height())

    @property
    def screen_name(self) -> str:
        return self.screen().name() if self.screen() is not None else ""

    def _on_tick(self, value) -> None:
        self._t = float(value)
        self.update()

    def _landed(self) -> None:
        landed, self._on_landed = self._on_landed, None
        if landed is not None and not self._thumb.isNull():
            top_left = self._screen_geo.topLeft()
            landed(self._dst.translated(top_left.x(), top_left.y()), self._thumb, self)
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
        picture = _centred(
            rect,
            _lerp(self._src.width(), self.picture_size[0], fly),
            _lerp(self._src.height(), self.picture_size[1], fly),
        )
        if not self._thumb.isNull():
            if fly > 0.0:
                for spread, alpha in ((10.0, 18), (5.0, 30), (2.0, 46)):
                    shadow = rect.adjusted(-spread, -spread + 4, spread, spread + 4)
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(QColor(0, 0, 0, int(alpha * fly)))
                    radius = _SNAP_RADIUS * fly + spread
                    painter.drawRoundedRect(shadow, radius, radius)
            _paint_card(painter, rect, self._thumb, _SNAP_RADIUS * fly, fly, picture)

        flash = 1.0 - _ease_out_cubic(t / _SNAP_FLASH_MS)
        if self._flash and flash > 0.0:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 255, 255, int(225 * flash)))
            painter.drawRect(self._src)
        painter.end()


#: The lifted picture under the pointer while the card is dragged: smaller
#: than the card so the drop target stays visible.
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
    w, h, _pw, _ph = _card_geometry(
        card.width(), card.height(), max_w=_DRAG_PREVIEW_W, max_h=_DRAG_PREVIEW_H,
        min_w=_DRAG_PREVIEW_W * 0.55, min_h=_DRAG_PREVIEW_H * 0.4,
    )
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


def _card_rect(screen, thumb: QImage) -> tuple[QRectF, tuple[float, float]]:
    """Where the resting card sits, and its picture's size inside it.

    The card is at the bottom-right of ``screen``'s work area, in absolute
    (virtual-desktop) logical coordinates. Used by a card that comes back
    after the editor; the thumbnail's pixels stand for the picture's size.
    """
    dpr = float(screen.devicePixelRatio() or 1.0)
    natural = (
        (thumb.width() / dpr, thumb.height() / dpr) if not thumb.isNull() else (320.0, 192.0)
    )
    tw, th, pw, ph = _card_geometry(*natural)
    # Inside the work area, so the card never sits under the taskbar.
    avail = screen.availableGeometry()
    right = float(avail.right() + 1)
    bottom = float(avail.bottom() + 1)
    rect = QRectF(right - tw - _SNAP_MARGIN, bottom - th - _SNAP_MARGIN, tw, th)
    return rect, (pw, ph)


def _stack_tops(
    bottom: float, top_limit: float, heights: list[float], *, gap: float, reserve: float = 0.0
) -> list[float | None]:
    """The top edge of every card in a corner stack; ``None`` = no room left.

    ``heights`` are newest first: the newest card sits on ``bottom`` and each
    older one ``gap`` above the card below it. ``reserve`` keeps that much
    space free at the bottom for a picture still flying in. Once one card no
    longer fits under ``top_limit``, no older card does either.
    """
    tops: list[float | None] = []
    cursor = bottom - reserve
    full = False
    for height in heights:
        top = cursor - height
        if full or top < top_limit:
            full = True
            tops.append(None)
            continue
        tops.append(top)
        cursor = top - gap
    return tops


#: Default card wording; the main process sends the user's language.
_CARD_LABELS = {
    "edit": "Edit",
    "copy": "Copy",
    "save": "Save",
    "close": "Close",
    "pin": "Keep on screen",
    "unpin": "Unpin",
    "copy_text": "Copy text",
}


class _CardWindow(QWidget):
    """The resting thumbnail with quick-access capture actions.

    Hover frosts the picture and shows Copy / Save stacked in the middle and
    four white chips in the corners: Pin (top-left, keeps the card until it is
    closed), Close (top-right), Edit (bottom-left) and Copy text (bottom-right,
    the on-screen text the appshot read). A click anywhere else edits, a drag
    shares the picture, a right-click dismisses it. ``rest_ms`` is how long it
    stays untouched — ``0`` keeps it until the user closes it.

    Each card belongs to one appshot (``appshot_id``) and holds its own
    finished picture (``image``), so in a stack every card copies, saves,
    drags and edits its own appshot. :meth:`move_to` glides it to its place.
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
        appshot_id: str = "",
        image: QImage | None = None,
        screen_name: str = "",
        picture_size: tuple[float, float] | None = None,
    ) -> None:
        super().__init__(None)
        #: The picture inside the card; ``None`` = as large as its aspect allows.
        self.picture_size = picture_size
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.Tool
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.appshot_id = appshot_id
        #: The finished (redacted) picture a drag and Copy text need; ``None``
        #: until ``snap_image`` arrived, and for good when none may be kept.
        self.image = image
        self.screen_name = screen_name
        #: The picture's size; the window is larger by ``_PAD`` on every side.
        self.card_size = (float(rect.width()), float(rect.height()))
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
        self._pinned = False  # pinned = stays until the user closes it
        self._frost: QImage | None = None  # blurred thumbnail behind the buttons
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
        # Position = place in the stack (``_origin``) + the sideways slide of
        # coming in / going out (``_slide``), so both can run at once.
        self._origin = self.pos()
        self._slide = 0.0
        self._target = self.pos()
        self._move_from = self.pos()
        self._move = QVariantAnimation(self)
        self._move.setStartValue(0.0)
        self._move.setEndValue(1.0)
        self._move.setDuration(_CARD_MOVE_MS)
        self._move.valueChanged.connect(self._on_move)

    @property
    def leaving(self) -> bool:
        return self._leaving

    def start(self, *, slide_in: bool = False) -> None:
        if slide_in:
            # Back from the editor: slide in from the edge it leaves through.
            self._on_out(1.0)
            self.show()
            self._in.start()
        else:
            self.show()
        self._arm_dismiss(self._rest_ms)

    def move_to(self, top_left: QPoint) -> None:
        """Glide to a new place in the stack (``top_left`` of the window)."""
        if top_left == self._target:
            return
        self._target = QPoint(top_left)
        self._move.stop()
        if not self.isVisible():
            self._origin = QPoint(top_left)
            self._apply_position()
            return
        self._move_from = QPoint(self._origin)
        self._move.start()

    def _on_move(self, value) -> None:
        t = _ease_out_cubic(float(value))
        a, b = self._move_from, self._target
        self._origin = QPoint(round(_lerp(a.x(), b.x(), t)), round(_lerp(a.y(), b.y(), t)))
        self._apply_position()

    def _arm_dismiss(self, ms: int) -> None:
        if self._rest_ms > 0 and not self._leaving and not self._pinned:
            self._dismiss.start(ms)

    # -- lifecycle -----------------------------------------------------------
    def leave(self) -> None:
        """Slide out and go. Safe to call more than once."""
        if self._leaving:
            return
        self._leaving = True
        self._dismiss.stop()
        self._in.stop()
        self._out.start()

    def finish_now(self) -> None:
        self._leaving = True
        self._dismiss.stop()
        self._in.stop()
        self._out.stop()
        self._move.stop()
        self._gone()

    def _on_out(self, value) -> None:
        self._slide = _ease_out_cubic(float(value))
        self._apply_position()

    def _apply_position(self) -> None:
        self.move(self._origin + QPoint(int(48 * self._slide), 0))
        self.setWindowOpacity(1.0 - self._slide)

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
        elif pressed == "pin":
            self._pinned = not self._pinned
            self._dismiss.stop()  # the pointer is on the card; leaving re-arms it
            self.update()
        elif pressed in ("copy", "save", "copy_text"):
            if self.image is not None:
                self._owner.card_action(self, pressed)
        else:  # "edit" or the picture itself
            self._owner.card_clicked(self)

    def _start_drag(self, grab: QPointF) -> None:
        owner = self._owner
        image = self.image
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
        # The card lifts off as a smaller, rounded copy
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
        if result == Qt.DropAction.IgnoreAction or self._pinned:
            # Dropped nowhere (or Esc), or pinned: the card stays. A pinned
            # card leaves only through its own Close.
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
        d, inset = _CARD_ROUND, _CARD_INSET
        left, right = rect.left() + inset, rect.right() - inset - d
        top, bottom = rect.top() + inset, rect.bottom() - inset - d
        buttons: dict[str, QRectF] = {
            "pin": QRectF(left, top, d, d),
            "close": QRectF(right, top, d, d),
            "edit": QRectF(left, bottom, d, d),
        }
        if self._owner is not None and self.image is not None:
            # Only with a kept, finished appshot — the same rule as the drag.
            buttons["copy_text"] = QRectF(right, bottom, d, d)
            metrics = QFontMetricsF(_pill_font())
            h, gap = _CARD_BUTTON_H, 8.0
            widths = [metrics.horizontalAdvance(self._labels[k]) + 36.0 for k in ("copy", "save")]
            width = max(widths + [88.0])
            if rect.height() >= 2 * h + gap + 2 * inset:
                # Stacked in the middle of the overlay.
                x = rect.center().x() - width / 2
                y = rect.center().y() - h - gap / 2
                buttons["copy"] = QRectF(x, y, width, h)
                buttons["save"] = QRectF(x, y + h + gap, width, h)
            else:  # a very flat card: side by side
                x = rect.center().x() - width - gap / 2
                y = rect.center().y() - h / 2
                buttons["copy"] = QRectF(x, y, width, h)
                buttons["save"] = QRectF(x + width + gap, y, width, h)
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
        picture = (
            _centred(rect, *self.picture_size) if self.picture_size is not None else None
        )
        _paint_card(painter, rect, self._thumb, _SNAP_RADIUS, 1.0, picture)
        if self._status:
            self._paint_scrim(painter, rect, 150)
            self._paint_centered(painter, rect, self._status)
        elif self._hover:
            self._paint_frost(painter, rect)
            buttons = self._buttons()
            self._paint_buttons(painter, buttons)
            self._paint_caption(painter, rect, buttons)
        elif self._pinned:
            # Pinned and at rest: the pin stays visible, so the card explains
            # why it does not go away.
            self._paint_chip(painter, "pin", self._buttons()["pin"])
        painter.end()

    def _paint_scrim(self, painter: QPainter, rect: QRectF, alpha: int) -> None:
        clip = QPainterPath()
        clip.addRoundedRect(rect, _SNAP_RADIUS, _SNAP_RADIUS)
        painter.save()
        painter.setClipPath(clip)
        painter.fillRect(rect, QColor(0, 0, 0, alpha))
        painter.restore()

    def _frosted(self) -> QImage:
        """The thumbnail blurred like frosted glass (cached).

        Shrinking in steps and growing back with smooth scaling is a cheap,
        dependency-free blur that is plenty for a 320 px card.
        """
        if self._frost is None:
            image = self._thumb
            size = image.size()
            for div in (4, 16):
                image = image.scaled(
                    max(1, size.width() // div),
                    max(1, size.height() // div),
                    Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            for div in (4, 1):
                image = image.scaled(
                    max(1, size.width() // div),
                    max(1, size.height() // div),
                    Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            self._frost = image
        return self._frost

    def _paint_frost(self, painter: QPainter, rect: QRectF) -> None:
        clip = QPainterPath()
        clip.addRoundedRect(rect, _SNAP_RADIUS, _SNAP_RADIUS)
        painter.save()
        painter.setClipPath(clip)
        if not self._thumb.isNull():
            painter.drawImage(rect, self._frosted())
        # A soft dark veil, then a faint light one: frosted, not black.
        painter.fillRect(rect, QColor(18, 16, 26, 92))
        painter.fillRect(rect, QColor(255, 255, 255, 18))
        painter.restore()
        pen = QPen(QColor(255, 255, 255, 150))
        pen.setWidthF(1.2)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect.adjusted(0.6, 0.6, -0.6, -0.6), _SNAP_RADIUS, _SNAP_RADIUS)

    def _paint_buttons(self, painter: QPainter, buttons: dict[str, QRectF]) -> None:
        painter.setFont(_pill_font())
        for name, area in buttons.items():
            if name not in ("copy", "save"):
                self._paint_chip(painter, name, area)
                continue
            hot = name == self._hot
            # Light pills on the frosted picture.
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(0, 0, 0, 34))
            radius = area.height() / 2
            painter.drawRoundedRect(area.translated(0, 1.5), radius, radius)
            painter.setBrush(QColor(255, 255, 255, 250) if hot else QColor(238, 238, 240, 238))
            painter.drawRoundedRect(area, radius, radius)
            painter.setPen(QColor(20, 20, 22))
            painter.drawText(area, Qt.AlignmentFlag.AlignCenter, self._labels[name])

    def _paint_chip(self, painter: QPainter, name: str, area: QRectF) -> None:
        """A round white corner chip with a dark icon; a set pin is inverted."""
        hot = name == self._hot and self._hover
        inverted = name == "pin" and self._pinned
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 40))
        painter.drawEllipse(area.translated(0, 1.2))
        if inverted:
            # A thin light ring keeps the dark chip visible on a dark picture.
            ring = QPen(QColor(255, 255, 255, 120))
            ring.setWidthF(1.0)
            painter.setPen(ring)
            painter.setBrush(QColor(22, 22, 26, 250 if hot else 232))
            icon = QColor(255, 255, 255)
        else:
            painter.setBrush(QColor(255, 255, 255, 252) if hot else QColor(240, 240, 242, 240))
            icon = QColor(18, 18, 20)
        painter.drawEllipse(area)
        _paint_icon(painter, name, area, icon)

    def _paint_caption(self, painter: QPainter, rect: QRectF, buttons: dict[str, QRectF]) -> None:
        """What the chip under the pointer does, else the card's hint.

        Sits between the two bottom chips, and only where it clears the pills.
        """
        if self._hot in ("pin", "close", "edit", "copy_text"):
            key = "unpin" if self._hot == "pin" and self._pinned else self._hot
            text = self._labels.get(key, "")
        else:
            text = self._hint
        if not text:
            return
        font = QFont()
        font.setPointSizeF(8.5)
        font.setWeight(QFont.Weight.Medium)
        metrics = QFontMetricsF(font)
        h = metrics.height() + 6.0
        side = _CARD_INSET + _CARD_ROUND + 6.0
        band = QRectF(
            rect.left() + side,
            rect.bottom() - _CARD_INSET - _CARD_ROUND / 2 - h / 2,
            rect.width() - 2 * side,
            h,
        )
        pills = [buttons[k] for k in ("copy", "save") if k in buttons]
        if band.width() <= 0 or any(p.bottom() + 2 > band.top() for p in pills):
            return
        painter.setFont(font)
        painter.setPen(QColor(255, 255, 255, 225))
        painter.drawText(
            band,
            Qt.AlignmentFlag.AlignCenter,
            metrics.elidedText(text, Qt.TextElideMode.ElideRight, band.width()),
        )

    def _paint_centered(self, painter: QPainter, rect: QRectF, text: str) -> None:
        font = _card_font()
        font.setPointSizeF(10.0)
        painter.setFont(font)
        painter.setPen(QColor(255, 255, 255, 245))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, text)


def _card_font() -> QFont:
    font = QFont()
    font.setPointSizeF(9.0)
    font.setWeight(QFont.Weight.DemiBold)
    return font


def _pill_font() -> QFont:
    font = QFont()
    font.setPointSizeF(10.5)
    font.setWeight(QFont.Weight.DemiBold)
    return font


def _poly(*points: tuple[float, float]) -> QPolygonF:
    return QPolygonF([QPointF(x, y) for x, y in points])


def _paint_icon(painter: QPainter, name: str, area: QRectF, color: QColor) -> None:
    """Draw a corner-chip icon on a 28-unit grid centred in ``area``.

    Drawn with paths instead of an icon font, so it looks the same on every
    OS and needs no asset.
    """
    painter.save()
    painter.translate(area.center())
    scale = area.width() / 28.0
    painter.scale(scale, scale)
    pen = QPen(color)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    if name == "close":
        pen.setWidthF(2.3)
        painter.setPen(pen)
        painter.drawLine(QPointF(-4.8, -4.8), QPointF(4.8, 4.8))
        painter.drawLine(QPointF(-4.8, 4.8), QPointF(4.8, -4.8))
    elif name == "pin":
        # An upright push pin, tilted so the head points top-right.
        painter.rotate(45.0)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(QRectF(-4.6, -9.0, 9.2, 2.8), 1.2, 1.2)
        painter.drawPolygon(_poly((-2.9, -6.6), (2.9, -6.6), (3.3, -1.2), (-3.3, -1.2)))
        painter.drawPolygon(_poly((-3.3, -1.4), (3.3, -1.4), (6.2, 2.2), (-6.2, 2.2)))
        pen.setWidthF(1.7)
        painter.setPen(pen)
        painter.drawLine(QPointF(0.0, 2.2), QPointF(0.0, 9.0))
    elif name == "edit":
        # A pencil, tip bottom-left.
        painter.rotate(45.0)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(QRectF(-2.9, -9.6, 5.8, 3.0), 1.2, 1.2)
        painter.drawRect(QRectF(-2.9, -5.4, 5.8, 8.6))
        painter.drawPolygon(_poly((-2.9, 4.4), (2.9, 4.4), (0.0, 9.4)))
    elif name == "copy_text":
        # A "T" inside scan corners: the text the appshot read.
        pen.setWidthF(1.7)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        e, k = 7.0, 3.2
        for sx, sy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
            corner = QPainterPath()
            corner.moveTo(sx * e, sy * (e - k))
            corner.lineTo(sx * e, sy * e)
            corner.lineTo(sx * (e - k), sy * e)
            painter.drawPath(corner)
        pen.setWidthF(2.0)
        painter.setPen(pen)
        painter.drawLine(QPointF(-3.2, -3.0), QPointF(3.2, -3.0))
        painter.drawLine(QPointF(0.0, -3.0), QPointF(0.0, 3.6))
    painter.restore()


class Renderer(QObject):
    """Owns the per-monitor windows, the animations, and the IPC slots."""

    def __init__(self, app: QApplication) -> None:
        super().__init__()
        self._app = app
        self._windows: list[_GlowWindow] = []
        self._snaps: list[_SnapWindow] = []
        #: The corner stack, oldest first; the newest sits at the bottom.
        self._cards: list[_CardWindow] = []
        self._card_hint = ""
        self._card_rest_ms = _CARD_REST_MS
        self._card_labels: dict[str, str] = {}
        self._hint = ""
        self._active = False  # "show" was requested and not yet "hide"
        #: The agent pointer (created on first use) and the system pointer swap.
        self._pointer: AgentPointerWindow | None = None
        self._cursor_hidden = False
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
                    self._set_pointer(bool(payload.get("pointer")))
                elif cmd == protocol.CMD_POINTER_PRESS:
                    if self._pointer is not None:
                        self._pointer.press()
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
                    card = self._card_for(str(payload.get("id", "") or ""))
                    if card is not None:
                        card.show_status(str(payload.get("text", "")))
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
        self._set_pointer(False)
        self._fade_to(0.0)

    def _set_pointer(self, wanted: bool) -> None:
        """Become (or stop being) the pointer while Jarvis operates the screen."""
        if wanted:
            if self._pointer is None:
                self._pointer = AgentPointerWindow()
            self._pointer.start()
            if not self._cursor_hidden:
                self._cursor_hidden = hide_system_cursor()
            return
        if self._pointer is not None:
            self._pointer.stop()
        if self._cursor_hidden:
            restore_system_cursor()
            self._cursor_hidden = False

    def shutdown(self) -> None:
        """The user's pointer always comes back, whatever ended the sidecar."""
        self._set_pointer(False)

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
        # A flight still under way lands at once and joins the stack; the
        # cards already there glide up to keep the bottom slot for this one.
        for old in list(self._snaps):
            old.land_now()
        self._card_hint = str(payload.get("hint", "") or "")
        self._take_card_options(payload)
        win = _SnapWindow(screen, rect, thumb, self._snap_landed, self._snap_done)
        self._snaps.append(win)
        self._relayout()
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

    def _new_card(
        self,
        rect: QRectF,
        thumb: QImage,
        *,
        screen_name: str,
        appshot_id: str = "",
        image: QImage | None = None,
        picture_size: tuple[float, float] | None = None,
    ) -> _CardWindow:
        """A new card at the bottom of the stack; the older ones move up."""
        card = _CardWindow(
            rect,
            thumb,
            self._card_hint,
            self,
            rest_ms=self._card_rest_ms,
            labels=self._card_labels,
            appshot_id=appshot_id,
            image=image,
            screen_name=screen_name,
            picture_size=picture_size,
        )
        self._cards.append(card)
        _emit(protocol.EVENT_CARD, open=True)
        self._relayout()
        return card

    def _snap_landed(self, rect: QRectF, thumb: QImage, flight: _SnapWindow) -> None:
        # The flight no longer holds the bottom slot: its card takes it.
        self._snap_done(flight)
        self._new_card(
            rect,
            thumb,
            screen_name=flight.screen_name,
            appshot_id=flight.appshot_id,
            image=flight.image,
            picture_size=flight.picture_size,
        ).start()

    def _relayout(self) -> None:
        """Put every card in its place: newest at the bottom, older ones above.

        Per screen; a flight still on its way keeps the bottom slot free.
        Cards beyond ``MAX_CARDS``, or without room on the screen, leave.
        """
        staying = [card for card in self._cards if not card.leaving]
        for card in staying[: max(0, len(staying) - protocol.MAX_CARDS)]:
            card.leave()
        staying = staying[-protocol.MAX_CARDS :]
        by_screen: dict[str, list[_CardWindow]] = {}
        for card in reversed(staying):  # newest first
            by_screen.setdefault(card.screen_name, []).append(card)
        for name, cards in by_screen.items():
            screen = _screen_named(name)
            if screen is None:
                continue
            avail = screen.availableGeometry()
            reserve = sum(
                snap.landing_height + _CARD_STACK_GAP
                for snap in self._snaps
                if snap.screen_name == name
            )
            tops = _stack_tops(
                float(avail.bottom() + 1) - _SNAP_MARGIN,
                float(avail.top()) + _SNAP_MARGIN,
                [card.card_size[1] for card in cards],
                gap=_CARD_STACK_GAP,
                reserve=reserve,
            )
            right = float(avail.right() + 1) - _SNAP_MARGIN
            pad = float(_CardWindow._PAD)
            for card, top in zip(cards, tops, strict=True):
                if top is None:
                    card.leave()
                    continue
                width, height = card.card_size
                place = QRectF(right - width, top, width, height).adjusted(-pad, -pad, pad, pad)
                card.move_to(place.toAlignedRect().topLeft())

    def _card_for(self, appshot_id: str) -> _CardWindow | None:
        """The card of that appshot; the newest card when no id is named."""
        live = [card for card in self._cards if not card.leaving] or self._cards
        if not appshot_id:
            return live[-1] if live else None
        return next((card for card in reversed(live) if card.appshot_id == appshot_id), None)

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
        appshot_id = str(payload.get("id", "") or "")
        for old in list(self._snaps):
            old.land_now()
        # The same appshot never shows twice: its old card makes way.
        for old in [c for c in self._cards if appshot_id and c.appshot_id == appshot_id]:
            old.finish_now()
        self._card_hint = str(payload.get("hint", "") or "")
        self._take_card_options(payload)
        origin = _flight_origin(payload.get("from"))
        if origin is not None:
            # Saved in the editor: the picture flies from the editor to the
            # bottom of the stack, the same flight as after the shutter.
            fly_screen, frac = origin
            win = _SnapWindow(
                fly_screen, frac, thumb, self._snap_landed, self._snap_done, flash=False
            )
            win.appshot_id = appshot_id
            self._snaps.append(win)
            self._relayout()
            win.start()
            return
        rect, picture_size = _card_rect(screen, thumb)
        self._new_card(
            rect,
            thumb,
            screen_name=screen.name(),
            appshot_id=appshot_id,
            picture_size=picture_size,
        ).start(slide_in=True)

    def card_action(self, card: _CardWindow, action: str) -> None:
        """A hover button: the main process copies or saves that card's appshot."""
        _emit(protocol.EVENT_CARD_ACTION, action=action, id=card.appshot_id)

    def _snap_image(self, payload: dict) -> None:
        """The finished picture: to the card (or flight) of its appshot.

        Without a card of that id, the newest one that has no appshot yet
        takes it — the shutter's card, whose id is only known after the
        capture. A picture nobody waits for is dropped; the controller sends
        it again after the next ``snap``.
        """
        raw = payload.get("image")
        if not isinstance(raw, str) or not raw:
            return
        image = QImage()
        if not image.loadFromData(QByteArray.fromBase64(raw.encode("ascii"))):
            return
        appshot_id = str(payload.get("id", "") or "")
        holders = [*reversed(self._snaps), *(c for c in reversed(self._cards) if not c.leaving)]
        holder = next(
            (h for h in holders if appshot_id and h.appshot_id == appshot_id),
            None,
        ) or next((h for h in holders if not h.appshot_id and h.image is None), None)
        if holder is None:
            return
        holder.appshot_id = holder.appshot_id or appshot_id
        holder.image = image
        if isinstance(holder, _CardWindow):
            holder.update()

    def card_gone(self, card: _CardWindow) -> None:
        with suppress(ValueError):
            self._cards.remove(card)
        if not self._cards:
            _emit(protocol.EVENT_CARD, open=False)
            return
        self._relayout()  # the cards above it glide down

    def card_clicked(self, card: _CardWindow) -> None:
        _emit(protocol.EVENT_SNAP_OPEN, id=card.appshot_id)
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
        # A flight still on its way lands now, so a quick next appshot only
        # stacks it instead of losing it; then every card hides.
        for snap in list(self._snaps):
            snap.land_now()
        for card in self._cards:
            card.hide()
        if not self._active:
            return
        self._blanked = True
        for win in self._windows:
            win.hide()
        if self._pointer is not None:
            self._pointer.set_blanked(True)

    def _unblank(self) -> None:
        for card in self._cards:
            if not card.isVisible():
                card.show()
        if not self._active or not self._blanked:
            return
        self._blanked = False
        for win in self._windows:
            win.show()
        if self._pointer is not None:
            self._pointer.set_blanked(False)

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
    # A sidecar that died while it held the pointer left the swap on record.
    restore_system_cursor(only_if_marked=True)

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

    try:
        return app.exec()
    finally:
        renderer.shutdown()


__all__ = ["Renderer", "run"]
