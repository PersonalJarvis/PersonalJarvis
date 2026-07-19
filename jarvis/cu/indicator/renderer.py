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
import threading
from contextlib import suppress

from PySide6.QtCore import (
    QEasingCurve,
    QObject,
    Qt,
    QVariantAnimation,
    Signal,
    Slot,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QGuiApplication,
    QLinearGradient,
    QPainter,
    QPen,
)
from PySide6.QtWidgets import QApplication, QWidget

from jarvis.cu.indicator import protocol
from jarvis.cu.indicator.win32 import exclude_from_capture, harden_window

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


class Renderer(QObject):
    """Owns the per-monitor windows, the animations, and the IPC slots."""

    def __init__(self, app: QApplication) -> None:
        super().__init__()
        self._app = app
        self._windows: list[_GlowWindow] = []
        self._hint = ""
        self._active = False  # "show" was requested and not yet "hide"
        self._blanked = False  # capture guard currently hiding the border

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
        cmd = payload["cmd"]
        if cmd == protocol.CMD_SHOW:
            self._show(str(payload.get("hint", "")))
        elif cmd == protocol.CMD_HIDE:
            self._hide()
        elif cmd == protocol.CMD_BLANK:
            # hide() reaches the platform window synchronously and the
            # subsequent grab travels through the same display-server
            # queue, so an immediate ack keeps ordering AND correctness.
            self._blank()
        elif cmd == protocol.CMD_UNBLANK:
            self._unblank()
        elif cmd == protocol.CMD_QUIT:
            _ack(cmd)
            self._app.quit()
            return
        _ack(cmd)

    # -- commands ----------------------------------------------------------
    def _show(self, hint: str) -> None:
        self._hint = hint
        self._active = True
        self._blanked = False
        self._ensure_windows()
        for win in self._windows:
            win.set_hint(hint)
            win.show()
        self._pulse.start()
        self._fade_to(1.0)

    def _hide(self) -> None:
        self._active = False
        self._fade_to(0.0)

    def _blank(self) -> None:
        if not self._active:
            return
        self._blanked = True
        for win in self._windows:
            win.hide()

    def _unblank(self) -> None:
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
