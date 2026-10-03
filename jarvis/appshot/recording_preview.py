"""Video corner cards in the shared screenshot effect process (Qt-only)."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QByteArray, QMimeData, QObject, QPoint, QRectF, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QDrag, QGuiApplication, QImage, QPainter
from PySide6.QtWidgets import QHBoxLayout, QLabel, QStyle, QToolButton, QVBoxLayout, QWidget

from jarvis.appshot.recording_geometry import elapsed_label
from jarvis.cu.indicator import protocol
from jarvis.cu.indicator import win32 as native_window


class RecordingCard(QWidget):
    """The same framed thumbnail as a screenshot; click plays, drag shares the MP4."""

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
        self.rest_ms = max(0, int(payload.get("rest_ms", 6000)))
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
        self.setGeometry(rect.adjusted(-12, -12, 12, 12).toAlignedRect())
        self.setMouseTracking(True)
        labels = payload.get("labels", {})
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 22, 22, 22)
        row = QHBoxLayout()
        self.pin = self.button(
            QStyle.StandardPixmap.SP_TitleBarShadeButton,
            labels.get("pin", "Keep on screen"),
            self.toggle_pin,
        )
        row.addWidget(self.pin)
        row.addStretch()
        row.addWidget(
            self.button(
                QStyle.StandardPixmap.SP_TitleBarCloseButton,
                labels.get("close", "Close"),
                self.close,
            )
        )
        layout.addLayout(row)
        layout.addStretch()
        self.play_button = self.button(
            QStyle.StandardPixmap.SP_MediaPlay, labels.get("play", "Play video"), self.play
        )
        self.play_button.setObjectName("appshot-recording-play")
        self.position_play_button()
        controls = QHBoxLayout()
        self.caption = QLabel(
            f"{labels.get('video', 'Video')} · {elapsed_label(float(payload.get('duration_s', 0)))}"
        )
        self.caption.setObjectName("appshot-recording-kind")
        self.caption.setAutoFillBackground(True)
        self.caption.setMargin(4)
        controls.addWidget(self.caption)
        controls.addStretch()
        controls.addWidget(
            self.button(
                QStyle.StandardPixmap.SP_DialogSaveButton, labels.get("save", "Save"), self.save
            )
        )
        layout.addLayout(controls)
        self.dismiss = QTimer(self)
        self.dismiss.setSingleShot(True)
        self.dismiss.timeout.connect(self.close)
        if self.rest_ms:
            self.dismiss.start(self.rest_ms)

    def button(self, icon, label, callback):
        button = QToolButton(self)
        button.setIcon(self.style().standardIcon(icon))
        button.setToolTip(label)
        button.setAccessibleName(label)
        button.setFixedSize(28, 28)
        button.setStyleSheet(
            "QToolButton { border-radius: 14px; background: palette(button); "
            "border: 1px solid palette(mid); } "
            "QToolButton:hover { background: palette(highlight); }"
        )
        button.clicked.connect(callback)
        return button

    def toggle_pin(self) -> None:
        self.pinned = not self.pinned
        self.pin.setCheckable(True)
        self.pin.setChecked(self.pinned)
        self.dismiss.stop()

    def position_play_button(self) -> None:
        if not hasattr(self, "play_button"):
            return
        diameter = min(52, max(28, self.height() - 96))
        self.play_button.setFixedSize(diameter, diameter)
        self.play_button.setIconSize(QSize(diameter // 2, diameter // 2))
        self.play_button.move((self.width() - diameter) // 2, (self.height() - diameter) // 2)
        self.play_button.setStyleSheet(
            f"QToolButton {{ border-radius: {diameter // 2}px; background: palette(button); "
            "border: 1px solid palette(mid); } "
            "QToolButton:hover { background: palette(highlight); }"
        )

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.position_play_button()

    def play(self) -> None:
        self.owner.emit(protocol.EVENT_RECORDING_OPEN, id=self.recording_id)

    def save(self) -> None:
        self.owner.emit(protocol.EVENT_RECORDING_SAVE, id=self.recording_id)

    def enterEvent(self, event) -> None:  # noqa: N802
        self.dismiss.stop()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        if not self.pinned and self.rest_ms:
            self.dismiss.start(2500)
        super().leaveEvent(event)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        harden = getattr(native_window, "harden_clickable_window", None)
        if harden is not None:
            harden(int(self.winId()))
        native_window.exclude_from_capture(int(self.winId()))

    def paintEvent(self, _event) -> None:  # noqa: N802
        from jarvis.cu.indicator.renderer import _paint_card

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        _paint_card(painter, QRectF(self.rect()).adjusted(12, 12, -12, -12), self.thumb, 12, 1)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.press = event.position().toPoint()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self.press is None or (event.position().toPoint() - self.press).manhattanLength() < 6:
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
        if event.button() == Qt.MouseButton.LeftButton and self.press is not None:
            self.press = None
            self.play()

    def closeEvent(self, event) -> None:  # noqa: N802
        self.dismiss.stop()
        if not self.closed:
            self.closed = True
            self.owner.remove(self)
        event.accept()
        self.deleteLater()


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
                card.caption.setText(text)
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
