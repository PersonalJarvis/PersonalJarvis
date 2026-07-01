"""EdgeGlowWindow — transparent click-through frameless window per screen.

Phase 9.3: the test square is gone; instead a ``QWebEngineView`` renders
``overlay-ui/dist/edge-glow.html``. A ``StateBridge`` is exposed via
``QWebChannel`` as ``stateBridge`` — the TS renderer subscribes to
``stateChanged`` and reads ``currentState``.

Window flags strictly follow Plan §12.1.

IMPORTANT: if ``overlay-ui/dist/edge-glow.html`` doesn't exist (e.g.
because it was never built), the view loads ``about:blank`` and logs a
warning. This prevents crashes in CI/smoke paths without a frontend build.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import (
    QObject,
    Qt,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtGui import QColor
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QVBoxLayout, QWidget

from .state import OverlayState, StateMachine
from .transparency import (
    apply_click_through,
    exclude_from_capture,
    reapply_capture_affinity,
)

if TYPE_CHECKING:  # pragma: no cover
    from PySide6.QtGui import QScreen

logger = logging.getLogger(__name__)


# Plan §22 — the Vite build places the HTML exactly here.
_EDGE_GLOW_HTML = (
    Path(__file__).resolve().parents[2] / "overlay-ui" / "dist" / "edge-glow.html"
)


class StateBridge(QObject):
    """QObject exposed via QWebChannel as ``stateBridge``.

    The TS renderer (``overlay-ui/src/edge-glow/main.ts``) connects
    its handler to ``stateChanged`` and calls ``currentState`` for the
    initial frame.

    This object MUST be instantiated on the GUI thread — Qt signals are
    automatically marshalled to the GUI thread slots via
    ``QueuedConnection`` when ``emit()`` is called from the IPC thread.
    """

    # (old, new, reason) — all three strings, since JS signal routing
    # only cleanly serializes primitive types.
    stateChanged = Signal(str, str, str)

    def __init__(
        self, machine: StateMachine, parent: Optional[QObject] = None
    ) -> None:
        super().__init__(parent)
        self._machine = machine
        # The subscriber is called synchronously on the StateMachine
        # caller thread. We only fire the Qt signal here — Qt marshals
        # it itself.
        self._unsubscribe = machine.subscribe(self._on_state_change)

    def _on_state_change(
        self,
        old: OverlayState,
        new: OverlayState,
        reason: Optional[str],
    ) -> None:
        try:
            self.stateChanged.emit(old.value, new.value, reason or "")
        except RuntimeError:
            # Window has already been destroyed -> the subscriber is
            # about to be detached; harmless.
            logger.debug("StateBridge.emit on dead QObject")

    @Slot(result=str)
    def currentState(self) -> str:
        return self._machine.state.value

    def shutdown(self) -> None:
        """Unsubscribe from the StateMachine — call before ``deleteLater()``."""
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None


class EffectsBridge(QObject):
    """QObject exposed via QWebChannel as ``effectsBridge``.
    Phase 9.5 — fires the action effects (click ripple, cursor trail,
    typing sweep).

    Signals go 1:1 to the TS renderer (Plan §14/§15/§16). Coords are
    always PHYSICAL px (Plan §14.4 + §11.2); the renderer converts via
    devicePixelRatio.
    """

    # x, y physical px, monitor_idx, button.
    clickEvent = Signal(int, int, int, str)
    # x, y physical px (from SHM or the WS fallback).
    cursorMoved = Signal(int, int)
    # kind, duration_hint_ms (-1 if not set).
    actionStarted = Signal(str, int)
    actionEnded = Signal()

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)

    def emit_click(self, x: int, y: int, monitor_idx: int, button: str) -> None:
        try:
            self.clickEvent.emit(int(x), int(y), int(monitor_idx), button)
        except RuntimeError:
            logger.debug("EffectsBridge click on dead QObject")

    def emit_cursor(self, x: int, y: int) -> None:
        try:
            self.cursorMoved.emit(int(x), int(y))
        except RuntimeError:
            logger.debug("EffectsBridge cursor on dead QObject")

    def emit_action_started(self, kind: str, duration_hint_ms: Optional[int]) -> None:
        try:
            self.actionStarted.emit(kind, int(duration_hint_ms or -1))
        except RuntimeError:
            logger.debug("EffectsBridge action_started on dead QObject")

    def emit_action_ended(self) -> None:
        try:
            self.actionEnded.emit()
        except RuntimeError:
            logger.debug("EffectsBridge action_ended on dead QObject")


class _TransparentWebPage(QWebEnginePage):
    """Page with a transparent background.

    Without this override, Chromium renders a white background even
    when ``body { background: transparent }`` is set. Plan §12.1 + AD-1
    require true compositor transparency.
    """

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.setBackgroundColor(QColor(0, 0, 0, 0))


class EdgeGlowWindow(QWidget):
    """Frameless, transparent, click-through, always on top.

    Geometry covers the full monitor. Unlike the 9.1 version, a
    ``QWebEngineView`` renders the real HTML/CSS pipeline here; the
    static test square has been removed.
    """

    def __init__(
        self,
        screen: "QScreen",
        *,
        hide_from_capture: bool = True,
        state_machine: Optional[StateMachine] = None,
        effects_bridge: Optional[EffectsBridge] = None,
        html_path: Optional[Path] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._screen = screen
        self._hide_from_capture = hide_from_capture
        self._state_machine = state_machine
        self._effects_bridge = effects_bridge
        self._html_path = html_path or _EDGE_GLOW_HTML
        # Plan §18.1 — the screenChanged signal is connected in showEvent,
        # since windowHandle() can be None before show().
        self._screen_change_connected: bool = False

        # Plan §12.1 — exakt diese Flag-Kombination.
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.Tool
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

        self.setScreen(screen)
        self.setGeometry(screen.geometry())

        # Layout + WebView. Margins 0 so the render surface uses the
        # full window geometry — otherwise there's a 9 px default margin.
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._view = QWebEngineView(self)
        # No background repaint from the widget itself.
        self._view.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._view.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        page = _TransparentWebPage(self._view)
        self._view.setPage(page)

        # WebChannel + StateBridge if a StateMachine was supplied.
        # This is optional so Phase 9.1 smoke tests keep working
        # without a machine. Phase 9.5 additionally attaches the
        # effectsBridge if ``effects_bridge`` is set.
        self._channel: Optional[QWebChannel] = None
        self._bridge: Optional[StateBridge] = None
        if state_machine is not None:
            self._channel = QWebChannel(page)
            self._bridge = StateBridge(state_machine, self)
            self._channel.registerObject("stateBridge", self._bridge)
            if effects_bridge is not None:
                self._channel.registerObject("effectsBridge", effects_bridge)
            page.setWebChannel(self._channel)

        # Load HTML — if it doesn't exist: about:blank + warning.
        if self._html_path.is_file():
            url = QUrl.fromLocalFile(str(self._html_path))
            self._view.setUrl(url)
        else:
            logger.warning(
                "edge-glow.html not found at %s — "
                "rendering about:blank (did you forget `npm run build` in overlay-ui/?)",
                self._html_path,
            )
            self._view.setUrl(QUrl("about:blank"))

        layout.addWidget(self._view)

    def showEvent(self, event) -> None:  # type: ignore[override]
        """Sets the Win32 affinity after the HWND exists."""
        super().showEvent(event)
        hwnd = int(self.winId())
        # Defense-in-depth: Qt sets WS_EX_TRANSPARENT via
        # WindowTransparentForInput — we set it again explicitly in case
        # a later setWindowFlags() call clears the bit.
        apply_click_through(hwnd)
        if self._hide_from_capture:
            exclude_from_capture(hwnd)
        # Plan §18.1 — re-apply on screenChanged + DPI changes.
        # We connect the signal here (after winId() exists), not in
        # __init__, since windowHandle() can be None before showEvent.
        if not self._screen_change_connected:
            handle = self.windowHandle()
            if handle is not None:
                handle.screenChanged.connect(self._on_screen_changed)
                self._screen_change_connected = True

    def _on_screen_changed(self, _screen) -> None:
        """Reapply affinity after a DPI change or monitor hotplug.
        Plan §18.1."""
        if not self._hide_from_capture:
            return
        try:
            hwnd = int(self.winId())
        except RuntimeError:
            return
        reapply_capture_affinity(hwnd)

    def set_view_visible(self, visible: bool) -> None:
        """Plan §17.3 — hide-on-5-min-idle sets the WebView's IsVisible
        to False so Chromium fully pauses the view."""
        self._view.setVisible(visible)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self._bridge is not None:
            self._bridge.shutdown()
        super().closeEvent(event)


__all__ = ["EdgeGlowWindow", "EffectsBridge", "StateBridge"]
