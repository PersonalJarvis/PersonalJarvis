"""MascotWindow — separates transparentes Frameless-Window. Plan §12.2 + §13.

Im Gegensatz zum EdgeGlowWindow ist Mascot NICHT click-through. Es
faengt Drag (linke Maustaste) und Right-Click (Kontextmenu).
QtWebEngineView rendert die Mascot-HTML (Rive oder PNG-Fallback).

Hit-Testing per Plan §12.2: setMask(QRegion ellipse) damit transparente
Ecken der 160x160 Box klick-passthrough sind.

Position-Persistence + Monitor-Recovery sind in mascot_position.py
ausgelagert; dieses Modul ist nur die Window-Mechanik.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

from PySide6.QtCore import (
    QObject,
    QPoint,
    QRect,
    Qt,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtGui import QAction, QColor, QRegion
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QMenu, QVBoxLayout, QWidget

from .mascot_position import (
    MascotPosition,
    clamp_to_work_area,
    snap_to_edges,
)
from .state import OverlayState, StateMachine
from .transparency import (
    apply_mascot_styles,
    exclude_from_capture,
    reapply_capture_affinity,
)

if TYPE_CHECKING:  # pragma: no cover
    from PySide6.QtGui import QMouseEvent

logger = logging.getLogger(__name__)


# Plan §22 — Vite-Build legt das HTML genau hier ab.
_MASCOT_HTML = (
    Path(__file__).resolve().parents[2] / "overlay-ui" / "dist" / "mascot.html"
)


class MascotStateBridge(QObject):
    """QWebChannel-Bridge fuer den Mascot. Plan §13.2 mappt
    OverlayStates auf Rive-Inputs.

    Wir feuern dieselbe stateChanged-Signatur wie StateBridge fuer den
    EdgeGlow, sodass der Mascot-Renderer dieselbe Bridge-Mechanik
    verwenden kann (typed Wrapper in bridge.ts).
    """

    stateChanged = Signal(str, str, str)  # old, new, reason

    def __init__(
        self, machine: StateMachine, parent: Optional[QObject] = None
    ) -> None:
        super().__init__(parent)
        self._machine = machine
        self._unsubscribe = machine.subscribe(self._on_state_change)

    def _on_state_change(
        self, old: OverlayState, new: OverlayState, reason: Optional[str]
    ) -> None:
        try:
            self.stateChanged.emit(old.value, new.value, reason or "")
        except RuntimeError:
            logger.debug("MascotStateBridge.emit on dead QObject")

    @Slot(result=str)
    def currentState(self) -> str:
        return self._machine.state.value

    def shutdown(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None


class _TransparentWebPage(QWebEnginePage):
    """Page mit transparentem Hintergrund. Identisch zur EdgeGlow-
    Variante — Chromium rendert sonst weiss obwohl body transparent."""

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.setBackgroundColor(QColor(0, 0, 0, 0))


# Position-Save Callback Signatur: (MascotPosition) -> None.
# Caller (setup_mascot) injiziert eine save-to-toml Funktion.
PositionSaveCallback = Callable[[MascotPosition], None]


class MascotWindow(QWidget):
    """Single-instance frameless transparent Window. Plan §12.2.

    Lifecycle::

        win = MascotWindow(
            initial_x=200, initial_y=80, monitor_name="\\\\.\\DISPLAY1",
            size_px=160, state_machine=machine,
            on_position_saved=lambda pos: save_position_to_toml(...),
        )
        win.show()
    """

    # Wird gefired wenn der User per Right-Click "Hide for session" waehlt.
    hideRequested = Signal()
    # User-triggered "Move to default" — Caller berechnet neue Position.
    resetRequested = Signal()
    # User-triggered "Settings..." — Caller oeffnet Settings-Dialog.
    settingsRequested = Signal()
    # User double-clicked the mascot. Caller forwards this upstream as
    # a MascotEventEnvelope(kind="mute_toggle") so the main jarvis
    # pipeline flips its mute flag. The window itself dims locally for
    # immediate visual feedback so the user does not wait for a round-
    # trip via the WS bridge.
    muteToggleRequested = Signal()

    def __init__(
        self,
        *,
        initial_x: int,
        initial_y: int,
        monitor_name: str,
        size_px: int = 160,
        snap_tolerance_px: int = 16,
        hide_from_capture: bool = True,
        state_machine: Optional[StateMachine] = None,
        on_position_saved: Optional[PositionSaveCallback] = None,
        html_path: Optional[Path] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._monitor_name = monitor_name
        self._size_px = size_px
        self._snap_tolerance = snap_tolerance_px
        self._hide_from_capture = hide_from_capture
        self._state_machine = state_machine
        self._on_position_saved = on_position_saved
        self._html_path = html_path or _MASCOT_HTML

        # Plan §12.2: KEIN WindowTransparentForInput (Klicks erwartet).
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        # KEIN WA_TransparentForMouseEvents — Mascot fangt Maus-Input.

        self.setFixedSize(size_px, size_px)
        # Plan §12.2: setMask(QRegion ellipse) damit transparente Ecken
        # nicht Klicks fangen. Re-applied in resizeEvent fuer Sicherheit.
        self.setMask(QRegion(QRect(0, 0, size_px, size_px), QRegion.RegionType.Ellipse))

        self.move(initial_x, initial_y)

        # Layout + WebView fuer Rive/PNG.
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._view = QWebEngineView(self)
        self._view.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        page = _TransparentWebPage(self._view)
        self._view.setPage(page)

        # WebChannel + StateBridge wenn StateMachine geliefert wurde.
        self._channel: Optional[QWebChannel] = None
        self._bridge: Optional[MascotStateBridge] = None
        if state_machine is not None:
            self._channel = QWebChannel(page)
            self._bridge = MascotStateBridge(state_machine, self)
            self._channel.registerObject("stateBridge", self._bridge)
            page.setWebChannel(self._channel)

        if self._html_path.is_file():
            self._view.setUrl(QUrl.fromLocalFile(str(self._html_path)))
        else:
            logger.warning(
                "mascot.html nicht gefunden unter %s — `npm run build` im overlay-ui/?",
                self._html_path,
            )
            self._view.setUrl(QUrl("about:blank"))

        layout.addWidget(self._view)

        # Drag-State.
        self._drag_offset: Optional[QPoint] = None
        self._dragging: bool = False
        # Local visual mute indicator — toggled on every doubleClick so the
        # sprite dims even before the round-trip through main jarvis lands.
        # Main jarvis stays the source of truth; if it disagrees we let
        # the next state-bridge event correct us.
        self._muted_visual: bool = False
        self._opacity_normal: float = 1.0
        self._opacity_muted: float = 0.45
        # Plan §18.1 — screenChanged-Hook wird in showEvent verbunden.
        self._screen_change_connected: bool = False

    # --- Lifecycle / Win32-Affinity ---

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        hwnd = int(self.winId())
        # Plan §12.2: Layered + NoActivate + Toolwindow extension flags.
        apply_mascot_styles(hwnd)
        if self._hide_from_capture:
            exclude_from_capture(hwnd)
        # Plan §18.1 — Reapply auf screenChanged.
        if not self._screen_change_connected:
            handle = self.windowHandle()
            if handle is not None:
                handle.screenChanged.connect(self._on_screen_changed)
                self._screen_change_connected = True

    def _on_screen_changed(self, _screen) -> None:
        if not self._hide_from_capture:
            return
        try:
            hwnd = int(self.winId())
        except RuntimeError:
            return
        reapply_capture_affinity(hwnd)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self._bridge is not None:
            self._bridge.shutdown()
        super().closeEvent(event)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        # Mask neu setzen falls jemand size_px aendert.
        self.setMask(
            QRegion(
                QRect(0, 0, self.width(), self.height()),
                QRegion.RegionType.Ellipse,
            )
        )

    # --- Drag (Plan §13.3) ---

    def mousePressEvent(self, event: "QMouseEvent") -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.pos()
            self._dragging = True
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event: "QMouseEvent") -> None:  # type: ignore[override]
        if self._dragging and self._drag_offset is not None:
            new_pos = event.globalPosition().toPoint() - self._drag_offset
            self.move(new_pos.x(), new_pos.y())
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: "QMouseEvent") -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            self._drag_offset = None
            self._finalize_drag()
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: "QMouseEvent") -> None:  # type: ignore[override]
        """Double-click toggles Jarvis-wide voice mute.

        Qt fires this between the second press and release of a left-button
        double-click. The first press of the pair already entered
        ``mousePressEvent`` and primed the drag-state, so we explicitly
        abort the drag here — otherwise an accidental cursor twitch
        between the second press and release would commit a move.
        """
        if event.button() != Qt.MouseButton.LeftButton:
            super().mouseDoubleClickEvent(event)
            return
        self._dragging = False
        self._drag_offset = None
        self._toggle_muted_visual()
        try:
            self.muteToggleRequested.emit()
        except RuntimeError:
            # Signal target died (Qt teardown race). Local visual flip
            # already happened so the user still sees feedback.
            logger.debug("muteToggleRequested.emit on dead QObject")
        event.accept()

    def _toggle_muted_visual(self) -> None:
        self._muted_visual = not self._muted_visual
        opacity = self._opacity_muted if self._muted_visual else self._opacity_normal
        try:
            self.setWindowOpacity(opacity)
        except RuntimeError:
            # Window torn down — safe to ignore. The opacity is a
            # cosmetic indicator only; main jarvis owns the truth.
            pass

    def set_muted_visual(self, muted: bool) -> None:
        """Authoritative state sync from main jarvis.

        Main jarvis will call this through the WebChannel bridge after it
        has flipped the actual mute flag, so the optimistic local toggle
        on doubleClick can be corrected if the two ever diverge (e.g.
        another trigger surface — hotkey, REST — flipped the flag).
        """
        if muted == self._muted_visual:
            return
        self._toggle_muted_visual()

    def contextMenuEvent(self, event) -> None:  # type: ignore[override]
        """Right-click QMenu — Plan §13.5 + §12.2."""
        menu = QMenu(self)
        mute_label = "Unmute Jarvis" if self._muted_visual else "Mute Jarvis"
        mute_action = QAction(mute_label, self)
        mute_action.triggered.connect(self._on_mute_menu)
        menu.addAction(mute_action)

        menu.addSeparator()

        hide_action = QAction("Hide for session", self)
        hide_action.triggered.connect(self.hideRequested.emit)
        menu.addAction(hide_action)

        reset_action = QAction("Reset position", self)
        reset_action.triggered.connect(self.resetRequested.emit)
        menu.addAction(reset_action)

        menu.addSeparator()

        settings_action = QAction("Settings...", self)
        settings_action.triggered.connect(self.settingsRequested.emit)
        menu.addAction(settings_action)

        menu.exec(event.globalPos())

    def _on_mute_menu(self) -> None:
        self._toggle_muted_visual()
        try:
            self.muteToggleRequested.emit()
        except RuntimeError:
            logger.debug("muteToggleRequested.emit on dead QObject")

    # --- Position-Persistence ---

    def _finalize_drag(self) -> None:
        """Snap-to-edge + Clamp + Persist. Plan §13.3 + §13.4."""
        from PySide6.QtGui import QGuiApplication

        # Welcher Monitor enthaelt das Mascot jetzt?
        center = self.pos() + QPoint(self.width() // 2, self.height() // 2)
        screen = QGuiApplication.screenAt(center) or QGuiApplication.primaryScreen()
        if screen is None:
            return  # extremly headless; ignore

        geo = screen.availableGeometry()
        monitor_geo = (geo.x(), geo.y(), geo.width(), geo.height())

        # Snap zuerst — Snap kann das Mascot ueber die Kante schieben,
        # daher danach clampen.
        snapped_x, snapped_y = snap_to_edges(
            self.x(),
            self.y(),
            monitor_geo,
            mascot_size_px=self._size_px,
            snap_tolerance_px=self._snap_tolerance,
        )
        clamped_x, clamped_y = clamp_to_work_area(
            snapped_x,
            snapped_y,
            monitor_geo,
            mascot_size_px=self._size_px,
        )
        if (clamped_x, clamped_y) != (self.x(), self.y()):
            self.move(clamped_x, clamped_y)

        # Relative zum work-area-Top-Left persistieren.
        rel_x = clamped_x - geo.x()
        rel_y = clamped_y - geo.y()
        position = MascotPosition(
            monitor=screen.name(),
            x_relative=rel_x,
            y_relative=rel_y,
        )
        self._monitor_name = screen.name()

        if self._on_position_saved is not None:
            try:
                self._on_position_saved(position)
            except Exception:  # noqa: BLE001
                logger.exception("on_position_saved raised")


__all__ = ["MascotStateBridge", "MascotWindow", "PositionSaveCallback"]
