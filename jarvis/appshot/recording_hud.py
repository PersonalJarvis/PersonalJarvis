"""Persistent, click-through capture boundary and its separate clickable stop bar.

Imported only by the recording sidecar, after QApplication has been created.
"""

from __future__ import annotations

import sys
from collections.abc import Callable

from PySide6.QtCore import QObject, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from jarvis.appshot.recording_geometry import elapsed_label, selected_rect, toolbar_rect
from jarvis.cu.indicator.win32 import exclude_from_capture, harden_window


def _tool_window(widget: QWidget, *, click_through: bool) -> None:
    flags = (
        Qt.WindowType.FramelessWindowHint
        | Qt.WindowType.WindowStaysOnTopHint
        | Qt.WindowType.WindowDoesNotAcceptFocus
        | Qt.WindowType.Tool
        | Qt.WindowType.NoDropShadowWindowHint
    )
    if click_through:
        flags |= Qt.WindowType.WindowTransparentForInput
        widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    widget.setWindowFlags(flags)
    widget.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    if sys.platform == "darwin":
        widget.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow)


class RecordingFrame(QWidget):
    def __init__(self, screen, selection: QRect, on_stop: Callable[[], None]) -> None:
        super().__init__()
        self.on_stop = on_stop
        self.setObjectName("appshot-recording-frame")
        _tool_window(self, click_through=True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setScreen(screen)
        self.selection = QRect(selection)
        self.setGeometry(selection.adjusted(-3, -3, 3, 3).intersected(screen.geometry()))
        self.capture_excluded = False

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        harden_window(int(self.winId()))
        self.capture_excluded = exclude_from_capture(int(self.winId()))

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        # The border sits outside the captured pixels wherever the monitor has
        # room. Full-screen edges stay visible just inside the monitor bounds.
        rect = QRectF(self.selection.translated(-self.x(), -self.y()))
        rect = rect.adjusted(-1.5, -1.5, 1.5, 1.5).intersected(
            QRectF(self.rect()).adjusted(1, 1, -1, -1)
        )
        painter.setPen(QPen(QColor(0, 0, 0, 150), 3))
        painter.drawRect(rect)
        painter.setPen(QPen(QColor(255, 255, 255, 235), 1))
        painter.drawRect(rect)

    def closeEvent(self, event) -> None:  # noqa: N802
        self.on_stop()
        event.accept()


class RecordingToolbar(QWidget):
    def __init__(self, labels: dict[str, str], on_stop: Callable[[], None]) -> None:
        super().__init__()
        self.on_stop = on_stop
        self.setObjectName("appshot-recording-toolbar")
        _tool_window(self, click_through=False)
        self.setAutoFillBackground(True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 7, 8, 7)
        layout.setSpacing(12)
        self.clock = QLabel("00:00")
        self.clock.setObjectName("appshot-recording-time")
        self.clock.setMinimumWidth(62)
        font = self.clock.font()
        font.setBold(True)
        self.clock.setFont(font)
        layout.addWidget(self.clock)
        self.stop_button = QPushButton(labels["stop"])
        self.stop_button.setObjectName("appshot-recording-stop")
        self.stop_button.clicked.connect(on_stop)
        layout.addWidget(self.stop_button)
        self.adjustSize()
        self.capture_excluded = False

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.capture_excluded = exclude_from_capture(int(self.winId()))

    def closeEvent(self, event) -> None:  # noqa: N802
        self.on_stop()
        event.accept()


class RecordingHud(QObject):
    def __init__(self, screen, fractions, labels, on_stop) -> None:
        super().__init__()
        geometry, available = screen.geometry(), screen.availableGeometry()
        rect = selected_rect(geometry.getRect(), tuple(fractions))
        self.frame = RecordingFrame(screen, QRect(*rect), on_stop)
        self.toolbar = RecordingToolbar(labels, on_stop)
        self.toolbar.setScreen(screen)
        self.toolbar.setGeometry(
            QRect(
                *toolbar_rect(
                    rect,
                    available.getRect(),
                    (self.toolbar.sizeHint().width(), self.toolbar.sizeHint().height()),
                )
            )
        )

    def show(self) -> None:
        self.frame.show()
        self.toolbar.show()

    def update_elapsed(self, seconds: float) -> None:
        text = elapsed_label(seconds)
        if self.toolbar.clock.text() != text:
            self.toolbar.clock.setText(text)

    def hide(self) -> None:
        self.frame.hide()
        self.toolbar.hide()

    def close(self) -> None:
        self.frame.close()
        self.toolbar.close()
