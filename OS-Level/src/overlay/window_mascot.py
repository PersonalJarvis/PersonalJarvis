"""MascotWindow — separate transparent frameless window. Plan §12.2 + §13.

Unlike EdgeGlowWindow, the mascot is NOT click-through. It catches
drag (left mouse button) and right-click (context menu).
QtWebEngineView renders the mascot HTML (Rive or PNG fallback).

Hit-testing per Plan §12.2: setMask(QRegion ellipse) so the transparent
corners of the 160x160 box are click-passthrough.

Position persistence + monitor recovery live in mascot_position.py;
this module is only the window mechanics.
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


# Plan §22 — the Vite build places the HTML exactly here.
_MASCOT_HTML = (
    Path(__file__).resolve().parents[2] / "overlay-ui" / "dist" / "mascot.html"
)


class MascotStateBridge(QObject):
    """QWebChannel bridge for the mascot. Plan §13.2 maps
    OverlayStates to Rive inputs.

    We fire the same stateChanged signature as StateBridge for the
    EdgeGlow, so the mascot renderer can use the same bridge mechanics
    (typed wrapper in bridge.ts).
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
    """Page with a transparent background. Identical to the EdgeGlow
    variant — otherwise Chromium renders white even though body is transparent."""

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.setBackgroundColor(QColor(0, 0, 0, 0))


# Position-save callback signature: (MascotPosition) -> None.
# The caller (setup_mascot) injects a save-to-toml function.
PositionSaveCallback = Callable[[MascotPosition], None]


class MascotWindow(QWidget):
    """Single-instance frameless transparent window. Plan §12.2.

    Lifecycle::

        win = MascotWindow(
            initial_x=200, initial_y=80, monitor_name="\\\\.\\DISPLAY1",
            size_px=160, state_machine=machine,
            on_position_saved=lambda pos: save_position_to_toml(...),
        )
        win.show()
    """

    # Fired when the user picks "Hide for session" via right-click.
    hideRequested = Signal()
    # User-triggered "Move to default" — the caller computes the new position.
    resetRequested = Signal()
    # User-triggered "Settings..." — the caller opens the settings dialog.
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

        # Plan §12.2: NO WindowTransparentForInput (clicks are expected).
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        # NO WA_TransparentForMouseEvents — the mascot catches mouse input.

        self.setFixedSize(size_px, size_px)
        # Plan §12.2: setMask(QRegion ellipse) so the transparent corners
        # don't catch clicks. Re-applied in resizeEvent for safety.
        self.setMask(QRegion(QRect(0, 0, size_px, size_px), QRegion.RegionType.Ellipse))

        self.move(initial_x, initial_y)

        # Layout + WebView for Rive/PNG.
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._view = QWebEngineView(self)
        self._view.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        page = _TransparentWebPage(self._view)
        self._view.setPage(page)

        # WebChannel + StateBridge if a StateMachine was supplied.
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
                "mascot.html not found at %s — did you run `npm run build` in overlay-ui/?",
                self._html_path,
            )
            self._view.setUrl(QUrl("about:blank"))

        layout.addWidget(self._view)

        # Drag state.
        self._drag_offset: Optional[QPoint] = None
        self._dragging: bool = False
        # Local visual mute indicator — toggled on every doubleClick so the
        # sprite dims even before the round-trip through main jarvis lands.
        # Main jarvis stays the source of truth; if it disagrees we let
        # the next state-bridge event correct us.
        self._muted_visual: bool = False
        self._opacity_normal: float = 1.0
        self._opacity_muted: float = 0.45
        # Plan §18.1 — the screenChanged hook is connected in showEvent.
        self._screen_change_connected: bool = False

    # --- Lifecycle / Win32-Affinity ---

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        hwnd = int(self.winId())
        # Plan §12.2: Layered + NoActivate + Toolwindow extension flags.
        apply_mascot_styles(hwnd)
        if self._hide_from_capture:
            exclude_from_capture(hwnd)
        # Plan §18.1 — reapply on screenChanged.
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
        # Re-apply the mask in case someone changes size_px.
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
        """Snap-to-edge + clamp + persist. Plan §13.3 + §13.4."""
        from PySide6.QtGui import QGuiApplication

        # Which monitor now contains the mascot?
        center = self.pos() + QPoint(self.width() // 2, self.height() // 2)
        screen = QGuiApplication.screenAt(center) or QGuiApplication.primaryScreen()
        if screen is None:
            return  # extremly headless; ignore

        geo = screen.availableGeometry()
        monitor_geo = (geo.x(), geo.y(), geo.width(), geo.height())

        # Snap first — snapping can push the mascot past the edge,
        # so clamp afterwards.
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

        # Persist relative to the work area's top-left.
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
