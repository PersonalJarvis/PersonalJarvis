"""EdgeGlowWindow — transparentes click-through Frameless-Fenster pro Screen.

Phase 9.3: Test-Quadrat ist raus, statt dessen rendert ein
``QWebEngineView`` die ``overlay-ui/dist/edge-glow.html``. Eine
``StateBridge`` wird via ``QWebChannel`` als ``stateBridge`` exposed —
der TS-Renderer subscribed auf ``stateChanged`` und liest ``currentState``.

Window-Flags strikt nach Plan §12.1.

WICHTIG: Wenn ``overlay-ui/dist/edge-glow.html`` nicht existiert (z.B.
weil noch nie gebuildet), laedt der View ``about:blank`` und loggt eine
Warnung. Das verhindert Crashes in CI/Smoke-Pfaden ohne Frontend-Build.
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


# Plan §22 — Vite-Build legt das HTML genau hier ab.
_EDGE_GLOW_HTML = (
    Path(__file__).resolve().parents[2] / "overlay-ui" / "dist" / "edge-glow.html"
)


class StateBridge(QObject):
    """QObject das via QWebChannel als ``stateBridge`` exposed wird.

    Der TS-Renderer (``overlay-ui/src/edge-glow/main.ts``) verbindet
    seinen Handler auf ``stateChanged`` und ruft ``currentState`` fuer das
    initiale Frame.

    Das Object MUSS im GUI-Thread instantiiert werden — Qt-Signals werden
    automatisch per ``QueuedConnection`` an die GUI-Thread-Slots
    marshalled, wenn ``emit()`` aus dem IPC-Thread kommt.
    """

    # (old, new, reason) — alle drei strings, weil JS-Signal-Routing nur
    # primitive Typen sauber serialisiert.
    stateChanged = Signal(str, str, str)

    def __init__(
        self, machine: StateMachine, parent: Optional[QObject] = None
    ) -> None:
        super().__init__(parent)
        self._machine = machine
        # Subscriber wird sync vom StateMachine-Caller-Thread aufgerufen.
        # Wir feuern hier nur das Qt-Signal — Qt marshalled es selbst.
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
            # Window wurde bereits zerstoert -> Subscriber wird gleich
            # detached; harmlos.
            logger.debug("StateBridge.emit on dead QObject")

    @Slot(result=str)
    def currentState(self) -> str:
        return self._machine.state.value

    def shutdown(self) -> None:
        """Unsubscribe vom StateMachine — aufrufen vor ``deleteLater()``."""
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None


class EffectsBridge(QObject):
    """QObject das via QWebChannel als ``effectsBridge`` exposed wird.
    Phase 9.5 — feuert die Action-Effects (Click-Ripple, Cursor-Trail,
    Typing-Sweep).

    Signale gehen 1:1 zum TS-Renderer (Plan §14/§15/§16). Coords sind
    immer PHYSICAL px (Plan §14.4 + §11.2); Renderer konvertiert per
    devicePixelRatio.
    """

    # x, y physical px, monitor_idx, button.
    clickEvent = Signal(int, int, int, str)
    # x, y physical px (aus SHM oder WS-Fallback).
    cursorMoved = Signal(int, int)
    # kind, duration_hint_ms (-1 wenn nicht gesetzt).
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
    """Page mit transparentem Hintergrund.

    Ohne diesen Override rendert Chromium einen weissen Background
    selbst wenn ``body { background: transparent }`` gesetzt ist. Plan
    §12.1 + AD-1 fordern echte Compositor-Transparenz.
    """

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.setBackgroundColor(QColor(0, 0, 0, 0))


class EdgeGlowWindow(QWidget):
    """Frameless, transparent, click-through, immer oben.

    Geometrie deckt den vollen Monitor ab. Im Gegensatz zur 9.1-Version
    rendert hier ein ``QWebEngineView`` die echte HTML/CSS-Pipeline; das
    statische Test-Quadrat ist entfernt.
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
        # Plan §18.1 — screenChanged-Signal wird in showEvent verbunden,
        # weil windowHandle() vor show() None sein kann.
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

        # Layout + WebView. Margins 0 damit der Render-Surface die volle
        # Window-Geometrie nutzt — sonst gibt es einen 9-px Default-Rand.
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._view = QWebEngineView(self)
        # Kein Background-Repaint vom Widget selbst.
        self._view.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._view.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        page = _TransparentWebPage(self._view)
        self._view.setPage(page)

        # WebChannel + StateBridge wenn StateMachine geliefert wurde.
        # Das ist optional damit Phase-9.1-Smoke-Tests ohne Machine
        # weiter laufen. Phase 9.5 haengt zusaetzlich die effectsBridge
        # an wenn ``effects_bridge`` gesetzt ist.
        self._channel: Optional[QWebChannel] = None
        self._bridge: Optional[StateBridge] = None
        if state_machine is not None:
            self._channel = QWebChannel(page)
            self._bridge = StateBridge(state_machine, self)
            self._channel.registerObject("stateBridge", self._bridge)
            if effects_bridge is not None:
                self._channel.registerObject("effectsBridge", effects_bridge)
            page.setWebChannel(self._channel)

        # HTML laden — wenn nicht vorhanden: about:blank + Warnung.
        if self._html_path.is_file():
            url = QUrl.fromLocalFile(str(self._html_path))
            self._view.setUrl(url)
        else:
            logger.warning(
                "edge-glow.html nicht gefunden unter %s — "
                "rendere about:blank (vergiss `npm run build` im overlay-ui/?)",
                self._html_path,
            )
            self._view.setUrl(QUrl("about:blank"))

        layout.addWidget(self._view)

    def showEvent(self, event) -> None:  # type: ignore[override]
        """Setzt Win32-Affinity nachdem das HWND existiert."""
        super().showEvent(event)
        hwnd = int(self.winId())
        # Defense-in-Depth: Qt setzt WS_EX_TRANSPARENT durch
        # WindowTransparentForInput — wir setzen es nochmal explizit, falls
        # ein spaeterer setWindowFlags-Call das Bit clearen sollte.
        apply_click_through(hwnd)
        if self._hide_from_capture:
            exclude_from_capture(hwnd)
        # Plan §18.1 — bei screenChanged + DPI-Wechseln re-applien.
        # Wir verbinden das Signal hier (nach winId() existiert), nicht
        # in __init__, weil windowHandle() vor showEvent None sein kann.
        if not self._screen_change_connected:
            handle = self.windowHandle()
            if handle is not None:
                handle.screenChanged.connect(self._on_screen_changed)
                self._screen_change_connected = True

    def _on_screen_changed(self, _screen) -> None:
        """Reapply Affinity nach DPI-Wechsel oder Monitor-Hotplug.
        Plan §18.1."""
        if not self._hide_from_capture:
            return
        try:
            hwnd = int(self.winId())
        except RuntimeError:
            return
        reapply_capture_affinity(hwnd)

    def set_view_visible(self, visible: bool) -> None:
        """Plan §17.3 — Hide-on-5-min-Idle macht WebView IsVisible False
        damit Chromium den View komplett pausiert."""
        self._view.setVisible(visible)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self._bridge is not None:
            self._bridge.shutdown()
        super().closeEvent(event)


__all__ = ["EdgeGlowWindow", "EffectsBridge", "StateBridge"]
