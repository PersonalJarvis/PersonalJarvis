"""Persistent, click-through capture boundary and its separate clickable stop bar.

Imported only by the recording sidecar, after QApplication has been created.
"""

from __future__ import annotations

import sys
from collections.abc import Callable

from PySide6.QtCore import QEasingCurve, QObject, QRect, QRectF, QSize, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
)

from jarvis.appshot.picker.annotate import _dark_hud
from jarvis.appshot.recording_geometry import elapsed_label, selected_rect, toolbar_rect
from jarvis.cu.indicator.win32 import (
    exclude_from_capture,
    harden_clickable_window,
    harden_window,
    strip_window_frame,
)

_RED = QColor(255, 69, 58)
#: The capture frame: ONE white line with rounded corners and nothing else —
#: no shadow ring, which read as a second, grey frame. Where the frame window
#: is excluded from capture, the line lies right ON the selection's edge and
#: hides the window border beneath it (radius 8 = a Windows 11 window corner),
#: so the two never show as a double frame. Elsewhere it stays fully outside
#: the captured pixels, with a radius small enough to clear the selection corners.
_LINE_WIDTH = 2.5
_ON_EDGE = (0.5, 8.0)  # line centre offset, corner radius
_OUTSIDE = (2.25, 4.5)
_FRAME_PAD = 12


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


def hud_style() -> str:
    """One look for every recording control: a rounded pill that follows the OS theme."""
    dark = _dark_hud()
    bg = "rgba(28, 28, 31, 242)" if dark else "rgba(250, 250, 252, 245)"
    edge = "rgba(255, 255, 255, 34)" if dark else "rgba(0, 0, 0, 30)"
    fg = "rgb(236, 236, 240)" if dark else "rgb(28, 28, 32)"
    muted = "rgba(236, 236, 240, 170)" if dark else "rgba(28, 28, 32, 160)"
    sep = "rgba(255, 255, 255, 40)" if dark else "rgba(0, 0, 0, 28)"
    ghost = "rgba(255, 255, 255, 20)" if dark else "rgba(0, 0, 0, 12)"
    ghost_hover = "rgba(255, 255, 255, 34)" if dark else "rgba(0, 0, 0, 22)"
    return f"""
        QFrame#pill {{ background: {bg}; border: 1px solid {edge}; border-radius: 14px; }}
        QFrame#sep {{ background: {sep}; border: none; }}
        QLabel {{ color: {fg}; background: transparent; }}
        QLabel#hint {{ color: {muted}; }}
        QPushButton {{
            color: {fg}; background: {ghost}; border: none; border-radius: 9px;
            padding: 6px 12px 6px 10px; font-weight: 600;
        }}
        QPushButton:hover {{ background: {ghost_hover}; }}
        QPushButton#stop {{ color: white; background: #E5342B; }}
        QPushButton#stop:hover {{ background: #F2463D; }}
        QPushButton#stop:pressed {{ background: #C82A22; }}
    """


def _shadow(widget: QWidget) -> None:
    shadow = QGraphicsDropShadowEffect(widget)
    shadow.setBlurRadius(26)
    shadow.setOffset(0, 5)
    shadow.setColor(QColor(0, 0, 0, 120))
    widget.setGraphicsEffect(shadow)


def _separator(parent: QWidget) -> QFrame:
    line = QFrame(parent)
    line.setObjectName("sep")
    line.setFixedSize(1, 20)
    return line


def _glyph(kind: str, colour: QColor, dpr: float) -> QIcon:
    """Small hand-drawn icons, so the controls never depend on an icon theme."""
    size = 14
    pixmap = QPixmap(round(size * dpr), round(size * dpr))
    pixmap.setDevicePixelRatio(dpr)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    if kind == "stop":
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(colour)
        painter.drawRoundedRect(QRectF(2.5, 2.5, 9, 9), 2, 2)
    else:  # "screen": a monitor outline with its stand
        pen = QPen(colour, 1.4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawRoundedRect(QRectF(1.5, 2, 11, 7.5), 1.6, 1.6)
        painter.drawLine(7, 9.5, 7, 12)
        painter.drawLine(4.5, 12.2, 9.5, 12.2)
    painter.end()
    return QIcon(pixmap)


def _font(widget: QWidget, px: int, weight: QFont.Weight, *, tabular: bool = False) -> QFont:
    font = QFont(widget.font())
    font.setPixelSize(px)
    font.setWeight(weight)
    if tabular:
        try:  # Qt >= 6.7: equal-width digits, so the clock does not wobble.
            font.setFeature(QFont.Tag("tnum"), 1)
        except (AttributeError, TypeError):
            pass  # Older Qt: the fixed clock width below still keeps the bar steady.
    return font


class RecordingDot(QWidget):
    """A softly pulsing red dot — the universal "recording now" signal."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setFixedSize(14, 14)
        self._level = 1.0
        self._pulse = QVariantAnimation(self)
        self._pulse.setStartValue(1.0)
        self._pulse.setKeyValueAt(0.5, 0.35)
        self._pulse.setEndValue(1.0)
        self._pulse.setDuration(1400)
        self._pulse.setLoopCount(-1)
        self._pulse.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._pulse.valueChanged.connect(self._set_level)
        self._pulse.start()

    def _set_level(self, value: float) -> None:
        self._level = float(value)
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        halo = QColor(_RED)
        halo.setAlpha(round(70 * (1.0 - self._level) + 20))
        painter.setBrush(halo)
        painter.drawEllipse(QRectF(0, 0, 14, 14))
        core = QColor(_RED)
        core.setAlpha(round(255 * (0.55 + 0.45 * self._level)))
        painter.setBrush(core)
        painter.drawEllipse(QRectF(3, 3, 8, 8))


class RecordingFrame(QWidget):
    def __init__(self, screen, selection: QRect, on_stop: Callable[[], None]) -> None:
        super().__init__()
        self.on_stop = on_stop
        self.setObjectName("appshot-recording-frame")
        _tool_window(self, click_through=True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setScreen(screen)
        self.selection = QRect(selection)
        pad = _FRAME_PAD
        self.setGeometry(selection.adjusted(-pad, -pad, pad, pad).intersected(screen.geometry()))
        self.capture_excluded = False

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        harden_window(int(self.winId()))
        # Otherwise Windows 11 outlines the padded window: a grey second frame.
        strip_window_frame(int(self.winId()))
        self.capture_excluded = exclude_from_capture(int(self.winId()))
        self.update()  # The line's placement depends on the exclusion.

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        # The frame sits outside the captured pixels wherever the monitor has
        # room. Full-screen edges stay visible just inside the monitor bounds.
        pad = _FRAME_PAD
        sel = QRectF(self.selection.translated(-self.x(), -self.y()))
        outer = sel.adjusted(-pad, -pad, pad, pad).intersected(QRectF(self.rect()))
        base = outer.adjusted(pad, pad, -pad, -pad)
        offset, corner = _ON_EDGE if self.capture_excluded else _OUTSIDE
        painter.setPen(QPen(QColor(255, 255, 255, 250), _LINE_WIDTH))
        painter.drawRoundedRect(base.adjusted(-offset, -offset, offset, offset), corner, corner)

    def closeEvent(self, event) -> None:  # noqa: N802
        self.on_stop()
        event.accept()


class RecordingToolbar(QWidget):
    """Pulsing dot, running time and a red stop button on one floating pill."""

    def __init__(
        self, labels: dict[str, str], on_stop: Callable[[], None], dpr: float = 1.0
    ) -> None:
        super().__init__()
        self.on_stop = on_stop
        self.setObjectName("appshot-recording-toolbar")
        _tool_window(self, click_through=False)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        # No edge line and no drop shadow: both drew a grey ring around the bar.
        self.setStyleSheet(hud_style() + "QFrame#pill { border: none; }")
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        pill = self._pill = QFrame(self)
        pill.setObjectName("pill")
        outer.addWidget(pill)
        layout = QHBoxLayout(pill)
        layout.setContentsMargins(14, 7, 7, 7)
        layout.setSpacing(10)
        self.dot = RecordingDot(pill)
        self.dot.setToolTip(labels["recording"])
        layout.addWidget(self.dot)
        self.clock = QLabel("00:00", pill)
        self.clock.setObjectName("appshot-recording-time")
        self.clock.setFont(_font(self.clock, 15, QFont.Weight.DemiBold, tabular=True))
        self.clock.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._fit_clock("00:00")
        layout.addWidget(self.clock)
        layout.addWidget(_separator(pill))
        self.stop_button = QPushButton(f" {labels['stop']}", pill)
        self.stop_button.setObjectName("stop")
        self.stop_button.setFont(_font(self.stop_button, 13, QFont.Weight.DemiBold))
        self.stop_button.setIcon(_glyph("stop", QColor(255, 255, 255), dpr))
        self.stop_button.setIconSize(QSize(14, 14))
        self.stop_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.stop_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.stop_button.clicked.connect(on_stop)
        layout.addWidget(self.stop_button)
        self.adjustSize()
        self.capture_excluded = False

    def _fit_clock(self, text: str) -> None:
        # Size for the widest digits, so ticking seconds never shift the button.
        metrics = self.clock.fontMetrics()
        widest = max("0123456789", key=metrics.horizontalAdvance)
        sample = "".join(widest if ch.isdigit() else ch for ch in text)
        self.clock.setFixedWidth(metrics.horizontalAdvance(sample) + 2)

    def set_clock(self, text: str) -> bool:
        """Show ``text``; True when the bar changed size (the hour digits appeared)."""
        grew = len(text) != len(self.clock.text())
        self.clock.setText(text)
        if grew:
            self._fit_clock(text)
            self._pill.layout().activate()
            self.layout().activate()
            self.adjustSize()
        return grew

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        # No Windows 11 frame around the bar, and a click never steals focus.
        harden_clickable_window(int(self.winId()))
        self.capture_excluded = exclude_from_capture(int(self.winId()))

    def closeEvent(self, event) -> None:  # noqa: N802
        self.on_stop()
        event.accept()


class RecordingHint(QFrame):
    """The selection-time pill: what to do, plus a one-click full-screen recording."""

    def __init__(
        self, parent: QWidget, labels: dict[str, str], on_full: Callable[[], None], dpr: float
    ) -> None:
        super().__init__(parent)
        self.setObjectName("pill")
        self.setStyleSheet(hud_style())
        self.setCursor(Qt.CursorShape.ArrowCursor)
        _shadow(self)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 7, 7, 7)
        layout.setSpacing(10)
        dot = QLabel(self)
        dot.setFixedSize(10, 10)
        dot.setStyleSheet(f"background: {_RED.name()}; border-radius: 5px;")
        layout.addWidget(dot)
        hint = QLabel(labels["select"], self)
        hint.setObjectName("hint")
        hint.setFont(_font(hint, 13, QFont.Weight.Medium))
        layout.addWidget(hint)
        layout.addWidget(_separator(self))
        dark = _dark_hud()
        self.full_button = QPushButton(f" {labels['full']}", self)
        self.full_button.setFont(_font(self.full_button, 13, QFont.Weight.DemiBold))
        self.full_button.setIcon(
            _glyph("screen", QColor(236, 236, 240) if dark else QColor(28, 28, 32), dpr)
        )
        self.full_button.setIconSize(QSize(14, 14))
        self.full_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.full_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.full_button.clicked.connect(lambda _checked=False: on_full())
        layout.addWidget(self.full_button)
        self.adjustSize()


class RecordingHud(QObject):
    def __init__(self, screen, fractions, labels, on_stop) -> None:
        super().__init__()
        geometry, self._available = screen.geometry(), screen.availableGeometry()
        self._rect = selected_rect(geometry.getRect(), tuple(fractions))
        self.frame = RecordingFrame(screen, QRect(*self._rect), on_stop)
        self.toolbar = RecordingToolbar(labels, on_stop, screen.devicePixelRatio())
        self.toolbar.setScreen(screen)
        self._place_toolbar()

    def _place_toolbar(self) -> None:
        hint = self.toolbar.sizeHint()
        x, y, w, h = toolbar_rect(
            self._rect, self._available.getRect(), (hint.width(), hint.height())
        )
        self.toolbar.setGeometry(QRect(x, y, w, h))

    def show(self) -> None:
        self.frame.show()
        self.toolbar.show()

    def update_elapsed(self, seconds: float) -> None:
        text = elapsed_label(seconds)
        if self.toolbar.clock.text() != text and self.toolbar.set_clock(text):
            self._place_toolbar()

    def hide(self) -> None:
        self.frame.hide()
        self.toolbar.hide()

    def close(self) -> None:
        self.frame.close()
        self.toolbar.close()
