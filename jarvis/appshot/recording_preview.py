"""Video corner cards in the shared screenshot effect process (Qt-only)."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QByteArray, QMimeData, QObject, QPoint, QPointF, QRectF, Qt, QTimer, QUrl
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
)
from PySide6.QtWidgets import QWidget

from jarvis.appshot.recording_geometry import elapsed_label
from jarvis.cu.indicator import protocol
from jarvis.cu.indicator import win32 as native_window
from jarvis.cu.indicator.renderer import (
    _CARD_INSET,
    _CARD_ROUND,
    _CARD_STATUS_MS,
    _SNAP_RADIUS,
    _card_font,
    _paint_card,
    _paint_icon,
    _poly,
)

#: The big play disc in the middle of a hovered card.
_PLAY_D = 46.0
#: Transparent margin around the picture that holds the soft shadow.
_PAD = 12


def _frosted(image: QImage) -> QImage:
    """The thumbnail blurred like frosted glass: shrink in steps, grow back smoothly."""
    size = image.size()
    for div in (4, 16, 4, 1):
        image = image.scaled(
            max(1, size.width() // div),
            max(1, size.height() // div),
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    return image


def _paint_video_icon(painter: QPainter, name: str, area: QRectF, color: QColor) -> None:
    """The play triangle and the save arrow, on the corner chips' 28-unit grid."""
    painter.save()
    painter.translate(area.center())
    scale = area.width() / 28.0
    painter.scale(scale, scale)
    if name == "play":
        # Rounded triangle, nudged right so it looks centred in the disc.
        pen = QPen(color, 2.4)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(color)
        painter.drawPolygon(_poly((-3.6, -6.4), (6.8, 0.0), (-3.6, 6.4)))
    else:  # "save": an arrow into a tray
        pen = QPen(color, 2.1)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawLine(QPointF(0.0, -7.0), QPointF(0.0, 3.0))
        painter.drawPolyline(_poly((-4.2, -1.2), (0.0, 3.0), (4.2, -1.2)))
        painter.drawPolyline(_poly((-6.8, 3.4), (-6.8, 7.0), (6.8, 7.0), (6.8, 3.4)))
    painter.restore()


def _caption_font() -> QFont:
    font = QFont()
    font.setPointSizeF(8.5)
    font.setWeight(QFont.Weight.Medium)
    return font


class RecordingCard(QWidget):
    """The same framed thumbnail as a screenshot card, for a finished video.

    At rest it shows a small duration badge. Hover frosts the picture and shows
    a big play disc in the middle and white chips in the corners: Pin
    (top-left), Close (top-right) and Save (bottom-right). A click anywhere
    else plays, a drag shares the MP4, a right-click dismisses the card.
    """

    def __init__(self, owner, screen, rect, thumb, payload) -> None:
        super().__init__()
        self.owner = owner
        self.screen_name = screen.name()
        self.recording_id = str(payload["id"])
        self.path = Path(payload["video_path"])
        self.thumb = thumb
        self.closed = False
        self.pinned = False
        self.press = None
        self.press_button = ""
        self.hover = False
        self.hot = ""
        self.frost: QImage | None = None
        self.status = ""
        self.rest_ms = max(0, int(payload.get("rest_ms", 6000)))
        self.labels = {
            "play": "Play video",
            "save": "Save video",
            "close": "Close",
            "pin": "Keep on screen",
            "video": "Video",
            **payload.get("labels", {}),
        }
        self.duration = elapsed_label(float(payload.get("duration_s", 0)))
        self.setObjectName("appshot-recording-card")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        if sys.platform == "darwin":
            self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow)
        self.setScreen(screen)
        self.setGeometry(rect.adjusted(-_PAD, -_PAD, _PAD, _PAD).toAlignedRect())
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)
        self.setAccessibleName(f"{self.labels['video']} · {self.duration}")
        self.dismiss = QTimer(self)
        self.dismiss.setSingleShot(True)
        self.dismiss.timeout.connect(self.close)
        self.status_timer = QTimer(self)
        self.status_timer.setSingleShot(True)
        self.status_timer.timeout.connect(self.clear_status)
        if self.rest_ms:
            self.dismiss.start(self.rest_ms)

    # -- actions -------------------------------------------------------------
    def play(self) -> None:
        self.owner.emit(protocol.EVENT_RECORDING_OPEN, id=self.recording_id)

    def save(self) -> None:
        self.owner.emit(protocol.EVENT_RECORDING_SAVE, id=self.recording_id)

    def toggle_pin(self) -> None:
        self.pinned = not self.pinned
        self.dismiss.stop()  # The pointer is on the card; leaving re-arms it.
        self.update()

    def show_status(self, text: str) -> None:
        """A short line over the card ("Saved to Downloads")."""
        self.status = text
        self.status_timer.start(_CARD_STATUS_MS)
        self.update()

    def clear_status(self) -> None:
        self.status = ""
        self.update()

    # -- geometry ------------------------------------------------------------
    def card_area(self) -> QRectF:
        return QRectF(_PAD, _PAD, self.width() - 2 * _PAD, self.height() - 2 * _PAD)

    def buttons(self) -> dict[str, QRectF]:
        """Hit areas of the hover buttons, in widget coordinates."""
        rect = self.card_area()
        d, inset = _CARD_ROUND, _CARD_INSET
        left, right = rect.left() + inset, rect.right() - inset - d
        top, bottom = rect.top() + inset, rect.bottom() - inset - d
        play = min(_PLAY_D, max(d, rect.height() - 2 * (d + inset) - 8))
        centre = rect.center()
        return {
            "pin": QRectF(left, top, d, d),
            "close": QRectF(right, top, d, d),
            "save": QRectF(right, bottom, d, d),
            "play": QRectF(centre.x() - play / 2, centre.y() - play / 2, play, play),
        }

    def button_at(self, pos: QPointF) -> str:
        if not self.hover:
            return ""
        for name, area in self.buttons().items():
            if area.adjusted(-2, -2, 2, 2).contains(pos):
                return name
        return ""

    # -- input ---------------------------------------------------------------
    def enterEvent(self, event) -> None:  # noqa: N802
        self.hover = True
        self.dismiss.stop()
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.hover = False
        self.hot = ""
        if not self.pinned and self.rest_ms:
            self.dismiss.start(2500)
        self.update()
        super().leaveEvent(event)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        harden = getattr(native_window, "harden_clickable_window", None)
        if harden is not None:
            harden(int(self.winId()))
        native_window.exclude_from_capture(int(self.winId()))

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.RightButton:
            self.close()
        elif event.button() == Qt.MouseButton.LeftButton:
            self.press = event.position().toPoint()
            self.press_button = self.button_at(event.position())

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        hot = self.button_at(event.position())
        if hot != self.hot:
            self.hot = hot
            self.update()
        if self.press is None or self.press_button:
            return
        if (event.position().toPoint() - self.press).manhattanLength() < 6:
            return
        self.press = None
        self.dismiss.stop()
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(self.path))])
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)
        if not self.pinned and self.rest_ms:
            self.dismiss.start(2500)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton or self.press is None:
            return
        self.press = None
        pressed, self.press_button = self.press_button, ""
        if pressed and pressed != self.button_at(event.position()):
            return  # Pressed a button, let go elsewhere: nothing.
        if pressed == "close":
            self.close()
        elif pressed == "pin":
            self.toggle_pin()
        elif pressed == "save":
            self.save()
        else:  # "play" or the picture itself
            self.play()

    def closeEvent(self, event) -> None:  # noqa: N802
        self.dismiss.stop()
        if not self.closed:
            self.closed = True
            self.owner.remove(self)
        event.accept()
        self.deleteLater()

    # -- painting ------------------------------------------------------------
    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        rect = self.card_area()
        painter.setPen(Qt.PenStyle.NoPen)
        for spread, alpha in ((8.0, 18), (4.0, 30), (1.5, 46)):
            painter.setBrush(QColor(0, 0, 0, alpha))
            shadow = rect.adjusted(-spread, -spread + 3, spread, spread + 3)
            painter.drawRoundedRect(shadow, _SNAP_RADIUS + spread, _SNAP_RADIUS + spread)
        _paint_card(painter, rect, self.thumb, _SNAP_RADIUS, 1.0)
        if self.status:
            self.paint_veil(painter, rect, None, QColor(0, 0, 0, 150))
            font = _card_font()
            font.setPointSizeF(10.0)
            painter.setFont(font)
            painter.setPen(QColor(255, 255, 255, 245))
            painter.drawText(
                rect, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self.status
            )
        elif self.hover:
            if self.frost is None and not self.thumb.isNull():
                self.frost = _frosted(self.thumb)
            self.paint_veil(painter, rect, self.frost, QColor(18, 16, 26, 92))
            buttons = self.buttons()
            for name in ("pin", "close", "save"):
                self.paint_chip(painter, name, buttons[name])
            self.paint_play(painter, buttons["play"])
            self.paint_caption(painter, rect)
        else:
            self.paint_badge(painter, rect)
            if self.pinned:
                # Pinned and at rest: the pin stays, so the card says why it stays.
                self.paint_chip(painter, "pin", self.buttons()["pin"])
        painter.end()

    def paint_veil(
        self, painter: QPainter, rect: QRectF, image: QImage | None, colour: QColor
    ) -> None:
        clip = QPainterPath()
        clip.addRoundedRect(rect, _SNAP_RADIUS, _SNAP_RADIUS)
        painter.save()
        painter.setClipPath(clip)
        if image is not None:
            painter.drawImage(rect, image)
        painter.fillRect(rect, colour)
        if image is not None:
            painter.fillRect(rect, QColor(255, 255, 255, 18))  # frosted, not black
        painter.restore()
        if image is not None:
            pen = QPen(QColor(255, 255, 255, 150))
            pen.setWidthF(1.2)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect.adjusted(0.6, 0.6, -0.6, -0.6), _SNAP_RADIUS, _SNAP_RADIUS)

    def paint_chip(self, painter: QPainter, name: str, area: QRectF) -> None:
        """A round white corner chip with a dark icon; a set pin is inverted."""
        hot = name == self.hot and self.hover
        inverted = name == "pin" and self.pinned
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 40))
        painter.drawEllipse(area.translated(0, 1.2))
        if inverted:
            ring = QPen(QColor(255, 255, 255, 120))
            ring.setWidthF(1.0)
            painter.setPen(ring)
            painter.setBrush(QColor(22, 22, 26, 250 if hot else 232))
            icon = QColor(255, 255, 255)
        else:
            painter.setBrush(QColor(255, 255, 255, 252) if hot else QColor(240, 240, 242, 240))
            icon = QColor(18, 18, 20)
        painter.drawEllipse(area)
        if name == "save":
            _paint_video_icon(painter, "save", area, icon)
        else:
            _paint_icon(painter, name, area, icon)

    def paint_play(self, painter: QPainter, area: QRectF) -> None:
        hot = self.hot == "play"
        if hot:
            area = area.adjusted(-2, -2, 2, 2)  # a small lift under the pointer
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 46))
        painter.drawEllipse(area.translated(0, 1.8))
        painter.setBrush(QColor(255, 255, 255, 252) if hot else QColor(242, 242, 244, 240))
        painter.drawEllipse(area)
        _paint_video_icon(painter, "play", area, QColor(18, 18, 20))

    def paint_caption(self, painter: QPainter, rect: QRectF) -> None:
        """What the button under the pointer does, else "Video · 00:12"."""
        if self.hot == "pin" and self.pinned:
            text = self.labels.get("unpin", self.labels["pin"])
        elif self.hot:
            text = self.labels.get(self.hot, "")
        else:
            text = f"{self.labels['video']} · {self.duration}"
        font = _caption_font()
        metrics = QFontMetricsF(font)
        h = metrics.height() + 6.0
        side = _CARD_INSET + _CARD_ROUND + 6.0
        band = QRectF(
            rect.left() + side,
            rect.bottom() - _CARD_INSET - _CARD_ROUND / 2 - h / 2,
            rect.width() - 2 * side,
            h,
        )
        if not text or band.width() <= 0 or self.buttons()["play"].bottom() + 2 > band.top():
            return
        painter.setFont(font)
        painter.setPen(QColor(255, 255, 255, 225))
        painter.drawText(
            band,
            Qt.AlignmentFlag.AlignCenter,
            metrics.elidedText(text, Qt.TextElideMode.ElideRight, band.width()),
        )

    def paint_badge(self, painter: QPainter, rect: QRectF) -> None:
        """A small dark pill, bottom-left: a play mark and the length."""
        font = _caption_font()
        font.setWeight(QFont.Weight.DemiBold)
        metrics = QFontMetricsF(font)
        h = 22.0
        icon = 9.0
        w = 8.0 + icon + 5.0 + metrics.horizontalAdvance(self.duration) + 9.0
        badge = QRectF(rect.left() + _CARD_INSET, rect.bottom() - _CARD_INSET - h, w, h)
        if badge.right() > rect.right() - _CARD_INSET:
            return
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(12, 12, 14, 190))
        painter.drawRoundedRect(badge, h / 2, h / 2)
        mark = QRectF(badge.left() + 8.0, badge.center().y() - icon / 2, icon, icon)
        painter.setBrush(QColor(255, 255, 255, 240))
        painter.drawPolygon(
            _poly(
                (mark.left() + 1.0, mark.top()),
                (mark.right(), mark.center().y()),
                (mark.left() + 1.0, mark.bottom()),
            )
        )
        painter.setFont(font)
        painter.setPen(QColor(255, 255, 255, 240))
        painter.drawText(
            badge.adjusted(8.0 + icon + 5.0, 0, 0, 0),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            self.duration,
        )


class RecordingPreviews(QObject):
    """A bounded bottom-right stack, using the screenshot flight animation unchanged."""

    def __init__(self, emit, activity) -> None:
        super().__init__()
        self.emit = emit
        self.activity = activity
        self.cards = []
        self.flights = []
        self.hidden = False

    @property
    def active(self) -> bool:
        return bool(self.cards or self.flights)

    def show(self, payload: dict) -> None:
        from jarvis.cu.indicator.renderer import _match_screen, _SnapWindow

        path = Path(str(payload.get("video_path", "")))
        if not path.is_file() or path.suffix.lower() != ".mp4":
            return
        thumb = QImage.fromData(
            QByteArray.fromBase64(str(payload.get("thumb", "")).encode("ascii"))
        )
        screen = next(
            (s for s in QGuiApplication.screens() if s.name() == payload.get("screen_name")), None
        )
        screen = screen or _match_screen(payload.get("monitor", []))
        if thumb.isNull() or screen is None:
            return
        for flight in list(self.flights):
            self.land(flight)
        while len(self.cards) >= protocol.MAX_CARDS:
            self.cards[0].close()

        def landed(rect, image, *_args):
            card = RecordingCard(self, screen, rect, image, payload)
            self.cards.append(card)
            self.layout_cards()
            if not self.hidden:
                card.show()
            self.activity()

        flight = _SnapWindow(
            screen,
            payload.get("rect", [0, 0, 1, 1]),
            thumb,
            landed,
            self.flight_done,
            corner="right",
        )
        self.flights.append(flight)
        self.activity()
        flight.start()

    def flight_done(self, flight) -> None:
        if flight in self.flights:
            self.flights.remove(flight)
        self.layout_cards()
        self.activity()

    @staticmethod
    def land(flight) -> None:
        flight._anim.stop()
        flight._landed()

    def remove(self, card) -> None:
        if card in self.cards:
            self.cards.remove(card)
        self.layout_cards()
        self.activity()

    def layout_cards(self) -> None:
        from jarvis.cu.indicator.renderer import _SNAP_MARGIN

        for screen in QGuiApplication.screens():
            available = screen.availableGeometry()
            bottom = available.bottom() + 1 - _SNAP_MARGIN
            for card in reversed(self.cards):
                if card.screen_name != screen.name():
                    continue
                height = card.height() - 24
                if bottom - height < available.top() + _SNAP_MARGIN:
                    card.close()  # remove() lays out the remaining bounded stack again.
                    return
                right = available.right() + 1 - _SNAP_MARGIN
                card.move(QPoint(right - card.width() + 12, bottom - height - 12))
                bottom -= height + 12

    def set_status(self, recording_id: str, text: str) -> bool:
        for card in self.cards:
            if card.recording_id == recording_id:
                card.show_status(text)
                return True
        return False

    def suspend(self, hidden: bool) -> None:
        self.hidden = hidden
        for flight in list(self.flights):
            self.land(flight)
        for card in self.cards:
            card.setVisible(not hidden)

    def close(self) -> None:
        for flight in list(self.flights):
            flight.finish_now()
        for card in list(self.cards):
            card.close()
