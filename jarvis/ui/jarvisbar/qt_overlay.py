"""Darwin Qt surface for the Jarvis Bar.

This module is intentionally separate from :mod:`jarvis.ui.jarvisbar.overlay`.
The Windows/Linux Tk surface has platform-specific behavior which must not be
disturbed, while Aqua-Tk 9 currently cannot clear a ``systemTransparent``
canvas reliably: its native ``NSWindow`` is non-opaque, but the Tk content
view still leaves an opaque black backing store and retains pixels from older
animation frames.

The companion process may select :class:`QtJarvisBarOverlay` on macOS.  It
reuses the deterministic Pillow renderer and the existing surface API, but
uses a Qt top-level window with a real alpha channel.  Every paint replaces
the complete backing image with ``CompositionMode_Source`` before drawing the
new RGBA frame.  Transparent pixels therefore clear both the initial backing
and the previous animation frame instead of blending over them.

PySide6 is imported lazily.  Importing this module remains safe in the base or
headless installation; only ``start()`` requires the desktop extra and a
usable display.  ``start()`` must run on the companion process's main thread.
"""

from __future__ import annotations

import logging
import os
import queue
import sys
import threading
import time
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

from PIL import Image

from jarvis.ui.jarvisbar import interaction, renderer

log = logging.getLogger("jarvis.ui.jarvisbar.qt")

# Keep the visual contract identical to the established Tk bar.  These values
# are local so importing the macOS surface never imports tkinter.
BAR_ALPHA = 0.6
AUDIBLE_LEVEL = 0.06
AUDIBLE_HOLD_S = 0.5
TARGET_FRAME_MS = 16
UI_QUEUE_INTERVAL_MS = 20
Z_ORDER_GUARD_INTERVAL_MS = 500
HOVER_POLL_INTERVAL_MS = 32
HOVER_HIT_SLOP_PX = 2
DRAG_THRESHOLD_PX = 16
MARGIN_PX = 12
TASKBAR_GAP_PX = 8
HANGUP_CLICK_GUARD_S = 1.0
_IDLE_SETTLE_TICKS = 30

# Cursor-monitor follow poll (ms). When enabled, the bar hops to whichever
# monitor the mouse is on, keeping its RELATIVE spot so a differently-sized
# monitor lines up (see interaction.project_relative). Cheap — one QCursor.pos()
# + one screenAt() per tick — and off the animation path.
CURSOR_MONITOR_POLL_MS = 250

_QT: SimpleNamespace | None = None

GeometryBounds = tuple[int, int, int, int]


def _prepare_macos_qt_process() -> None:
    """Keep the companion from becoming macOS' foreground application.

    Qt's Cocoa plugin normally activates a command-line ``QApplication`` when
    launch finishes.  That is correct for a regular app, but an overlay helper
    becoming active makes macOS consume the user's next browser/editor click
    merely to reactivate that app.  This process owns no normal application
    window, so disable the transform before constructing ``QApplication``.
    """
    if sys.platform == "darwin":
        os.environ["QT_MAC_DISABLE_FOREGROUND_APPLICATION_TRANSFORM"] = "1"


def _enable_macos_first_mouse(native_view: Any, objc_module: Any) -> bool:
    """Let the inactive companion panel receive its first mouse-down.

    AppKit normally consumes the first click on a view in an inactive
    application and uses it only to activate that application.  The Jarvis Bar
    intentionally lives in a non-activating accessory process, so that default
    turns the advertised single-click talk action into an accidental
    double-click.  Qt does not expose ``NSView.acceptsFirstMouse:``; install the
    narrow native override on Qt's bar-view class instead.

    The companion owns only this overlay window, so the class-level override is
    process-local and cannot alter the desktop app or other applications.
    Failure is non-fatal: the panel keeps all of its existing rendering,
    hover, drag, mute, and hang-up behavior.
    """

    try:
        inherited = getattr(native_view, "acceptsFirstMouse_", None)
        signature = getattr(inherited, "signature", b"c@:@")

        def _accepts_first_mouse(_view: Any, _event: Any) -> bool:
            return True

        method = objc_module.selector(
            _accepts_first_mouse,
            selector=b"acceptsFirstMouse:",
            signature=signature,
        )
        objc_module.classAddMethod(
            native_view.__class__,
            b"acceptsFirstMouse:",
            method,
        )
        log.info("Qt Jarvis Bar native first-click handling enabled")
        return True
    except Exception:  # noqa: BLE001 - click handling degrades to the Qt default
        log.warning(
            "Qt Jarvis Bar could not enable first-click handling; the macOS "
            "panel may require a second click",
            exc_info=True,
        )
        return False


def _geometry_bounds(geometry: Any) -> GeometryBounds:
    """Return one Qt-like rectangle as ``(x, y, width, height)``."""
    return (
        int(geometry.x()),
        int(geometry.y()),
        int(geometry.width()),
        int(geometry.height()),
    )


def _dock_reserved_edge(
    full: GeometryBounds,
    available: GeometryBounds,
) -> str | None:
    """Infer which non-menu edge macOS reserved for the Dock."""
    full_x, full_y, full_w, full_h = full
    avail_x, _avail_y, avail_w, avail_h = available
    gaps = {
        "left": max(0, avail_x - full_x),
        "right": max(0, full_x + full_w - (avail_x + avail_w)),
        "bottom": max(0, full_y + full_h - (_avail_y + avail_h)),
    }
    edge, gap = max(gaps.items(), key=lambda item: item[1])
    return edge if gap else None


def _expand_geometry_for_hidden_dock(
    full: GeometryBounds,
    available: GeometryBounds,
) -> GeometryBounds:
    """Restore only the hidden Dock edge while preserving the menu-bar inset."""
    full_x, full_y, full_w, full_h = full
    avail_x, avail_y, avail_w, avail_h = available
    edge = _dock_reserved_edge(full, available)
    if edge == "left":
        return full_x, avail_y, max(0, avail_x + avail_w - full_x), avail_h
    if edge == "right":
        return avail_x, avail_y, max(0, full_x + full_w - avail_x), avail_h
    if edge == "bottom":
        return avail_x, avail_y, avail_w, max(0, full_y + full_h - avail_y)
    return available


def _rectangles_overlap(first: GeometryBounds, second: GeometryBounds) -> bool:
    first_x, first_y, first_w, first_h = first
    second_x, second_y, second_w, second_h = second
    return (
        first_w > 0
        and first_h > 0
        and second_w > 0
        and second_h > 0
        and first_x < second_x + second_w
        and second_x < first_x + first_w
        and first_y < second_y + second_h
        and second_y < first_y + first_h
    )


def _macos_dock_is_visible_on_screen(screen: GeometryBounds) -> bool | None:
    """Return whether Quartz currently exposes a visible Dock on ``screen``.

    ``QScreen.availableGeometry()`` keeps reserving the Dock strip while a
    fullscreen Space hides it. Quartz's on-screen window catalogue reflects
    the actual presentation state. ``None`` is deliberately fail-safe: if the
    optional native framework or query is unavailable, callers retain Qt's
    conservative available geometry.
    """
    if sys.platform != "darwin":
        return None
    try:
        import Quartz  # type: ignore[import-not-found] # noqa: PLC0415

        options = (
            Quartz.kCGWindowListOptionOnScreenOnly
            | Quartz.kCGWindowListExcludeDesktopElements
        )
        windows = Quartz.CGWindowListCopyWindowInfo(
            options,
            Quartz.kCGNullWindowID,
        )
    except Exception:  # noqa: BLE001 - optional native capability
        return None

    for window in windows or ():
        if str(window.get("kCGWindowOwnerName") or "") != "Dock":
            continue
        try:
            if float(window.get("kCGWindowAlpha", 1.0) or 0.0) <= 0.0:
                continue
            bounds = window.get("kCGWindowBounds") or {}
            dock_bounds = (
                int(bounds.get("X", 0)),
                int(bounds.get("Y", 0)),
                int(bounds.get("Width", 0)),
                int(bounds.get("Height", 0)),
            )
        except (AttributeError, TypeError, ValueError):
            continue
        if _rectangles_overlap(screen, dock_bounds):
            return True
    return False


def _qt() -> SimpleNamespace:
    """Return the lazily imported Qt modules.

    Keeping this behind a function preserves the torch-/GUI-free base import
    floor.  The caller owns the honest missing-desktop-extra diagnostic.
    """
    global _QT
    if _QT is None:
        from PySide6 import QtCore, QtGui, QtWidgets  # noqa: PLC0415

        _QT = SimpleNamespace(
            QtCore=QtCore,
            QtGui=QtGui,
            QtWidgets=QtWidgets,
            Qt=QtCore.Qt,
        )
    return _QT


def _qimage_from_pil(image: Image.Image) -> Any:
    """Copy a Pillow RGBA image into an owning ``QImage``."""
    q = _qt()
    rgba = image.convert("RGBA")
    width, height = rgba.size
    raw = rgba.tobytes("raw", "RGBA")
    wrapped = q.QtGui.QImage(
        raw,
        width,
        height,
        width * 4,
        q.QtGui.QImage.Format.Format_RGBA8888,
    )
    # The wrapper above borrows ``raw``.  A detached copy is load-bearing:
    # Pillow/bytes may be released before Qt services the asynchronous paint.
    return wrapped.copy()


def _paint_transparent_frame(
    painter: Any,
    rect: Any,
    image: Any | None,
) -> None:
    """Replace a complete paint device with one transparent RGBA frame.

    ``SourceOver`` cannot clear pixels left by a prior frame: an alpha-zero
    source is a no-op.  First filling with transparent black under ``Source``
    replaces every destination pixel, including its alpha.  Drawing the new
    image afterwards gives the compositor a fresh frame with no black backing
    and no animation trails.
    """
    q = _qt()
    painter.setCompositionMode(q.QtGui.QPainter.CompositionMode.CompositionMode_Source)
    painter.fillRect(rect, q.QtGui.QColor(0, 0, 0, 0))
    if image is None:
        return
    painter.setCompositionMode(q.QtGui.QPainter.CompositionMode.CompositionMode_SourceOver)
    painter.drawImage(rect, image)


def _input_mask_from_frame(image: Any) -> Any:
    """Return a native input mask containing only non-transparent pixels.

    A translucent top-level window still owns its complete rectangular mouse
    region on macOS.  Without an explicit mask, the invisible padding around
    the pill consumes clicks intended for the app underneath it.  The renderer
    produces binary alpha (fully clear key pixels, fully opaque bar pixels), so
    its alpha mask is also the exact hit-test shape we need.
    """
    q = _qt()
    bitmap = q.QtGui.QBitmap.fromImage(image.createAlphaMask())
    return q.QtGui.QRegion(bitmap)


def _window_class() -> type:
    """Build the QWidget subclass after the lazy PySide6 import."""
    q = _qt()

    class _QtBarWindow(q.QtWidgets.QWidget):
        def __init__(self, owner: QtJarvisBarOverlay) -> None:
            super().__init__(None)
            self._owner = owner
            flags = (
                q.Qt.WindowType.FramelessWindowHint
                | q.Qt.WindowType.WindowStaysOnTopHint
                | q.Qt.WindowType.Tool
                | q.Qt.WindowType.NoDropShadowWindowHint
                | q.Qt.WindowType.WindowDoesNotAcceptFocus
            )
            self.setWindowFlags(flags)
            self.setAttribute(q.Qt.WidgetAttribute.WA_TranslucentBackground)
            self.setAttribute(q.Qt.WidgetAttribute.WA_ShowWithoutActivating)
            # A macOS Qt::Tool is an NSPanel and normally disappears while its
            # process is inactive.  The companion is intentionally an accessory
            # process, so the overlay must remain visible above the active app.
            mac_always_show = getattr(q.Qt.WidgetAttribute, "WA_MacAlwaysShowToolWindow", None)
            if mac_always_show is not None:
                self.setAttribute(mac_always_show)
            self.setMouseTracking(True)
            self.setFixedSize(renderer.WIN_W, renderer.WIN_H)
            self.setWindowOpacity(owner._opacity)

        def paintEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
            del event
            painter = q.QtGui.QPainter(self)
            try:
                _paint_transparent_frame(painter, self.rect(), self._owner._qimage)
            finally:
                painter.end()

        def enterEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
            self._owner._hover_enter_ui()
            super().enterEvent(event)

        def leaveEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
            self._owner._hover_leave_ui()
            super().leaveEvent(event)

        def mousePressEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
            if self._owner._mouse_press_ui(event):
                event.accept()
                return
            super().mousePressEvent(event)

        def mouseMoveEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
            # A native mouse move is also proof that the pointer reached the
            # masked bar, even if a non-activating NSPanel omitted Enter.
            self._owner._hover_enter_ui()
            if self._owner._mouse_move_ui(event):
                event.accept()
                return
            super().mouseMoveEvent(event)

        def mouseReleaseEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
            if self._owner._mouse_release_ui(event):
                event.accept()
                return
            super().mouseReleaseEvent(event)

    return _QtBarWindow


class QtJarvisBarOverlay:
    """Main-thread Qt implementation of the Jarvis Bar surface contract.

    The class is constructed before a ``QApplication`` exists.  All Qt objects
    are created inside ``start()``, which lets the existing companion-host
    lifecycle instantiate and wire callbacks first.  Public methods may be
    called from the host's stdin-reader thread; widget mutations are queued and
    drained by a timer on the Qt main thread.
    """

    def __init__(
        self,
        persistent: bool = True,
        accent: str = "#e7c46e",
        opacity: float = BAR_ALPHA,
        startup_gated: bool = False,
        size_scale: float = 1.0,
        follow_cursor_monitor: bool = True,
    ) -> None:
        self._persistent_flag = bool(persistent)
        self._accent = accent
        self._opacity = max(0.2, min(1.0, float(opacity)))
        self._startup_gated = bool(startup_gated)
        # User "Bar size" preference (multiplied on top of the screen-adaptive
        # scale) + the screen scale captured at start(), so the live "Bar size"
        # slider can re-derive geometry without re-probing the screen.
        self._user_size_scale = renderer.clamp_user_size(size_scale)
        self._screen_scale = 1.0
        self._mode = "idle"
        self._ext_level = 0.0
        self._last_audible_t = 0.0
        self._muted = False
        self._hovered = False
        self._static_tick_key: tuple[str, bool, bool] | None = None
        self._static_tick_count = 0
        self._hangup_click_block_until = 0.0

        self._ui_queue: queue.Queue[Callable[[], None]] = queue.Queue()
        self._started = threading.Event()
        self._running = False
        self._stop_requested = False
        self._desired_visible = self._persistent_flag and not self._startup_gated
        self._t0 = 0.0
        self._last_frame_ns = 0

        self._app: Any = None
        self._window: Any = None
        # Kept for duck-typed code which probes Tk surfaces.  A Qt host has no
        # Tk root; reset remains available through ``_on_reset_double_click``.
        self._root: Any = None
        self._renderer: renderer.JarvisBarRenderer | None = None
        self._qimage: Any = None
        self._input_mask: Any = None
        self._input_mask_key: bytes | None = None
        self._frame_timer: Any = None
        self._queue_timer: Any = None
        self._z_timer: Any = None
        self._hover_timer: Any = None
        self._cursor_timer: Any = None
        self._native_window: Any = None
        self._x = 0
        self._y = 0
        # Keep the user's requested location separate from the temporary safe
        # location. When a hidden Dock returns, the bar retreats above it; when
        # the Dock hides again, the requested location is restored automatically.
        self._preferred_position: tuple[int, int] | None = None
        # Multi-monitor follow: migrate the bar to the monitor under the mouse.
        # ``_rel_pos`` is the monitor-independent free-space fraction (the
        # persisted placement truth); ``_cur_work`` is the work area the bar
        # currently sits on, so the poll can tell when the cursor's monitor
        # differs. Both seeded in ``_resolve_position_ui``.
        self._follow_cursor = bool(follow_cursor_monitor)
        self._rel_pos: tuple[float, float] | None = None
        self._cur_work: GeometryBounds | None = None
        self._drag: dict[str, Any] | None = None

        self._on_mute_toggle: Callable[[], None] | None = None
        self._feedback_publisher: Callable[[str, dict], None] | None = None
        self._on_show_window: Callable[[], None] | None = None
        # The companion has no SpeechPipeline of its own.  The host should map
        # this callback to an IPC event and let the parent perform the action.
        self._on_voice_action: Callable[[str], None] | None = None

    # ------------------------------------------------------------------
    # Surface API consumed by OrbBusBridge / the companion host
    # ------------------------------------------------------------------
    @property
    def _persistent(self) -> bool:
        return self._persistent_flag

    @_persistent.setter
    def _persistent(self, enabled: bool) -> None:
        self._persistent_flag = bool(enabled)
        self._desired_visible = not self._startup_gated and (
            self._persistent_flag or self._mode != "idle"
        )
        self._enqueue_if_started(self._sync_visibility_ui)

    def show(self, mode: str = "listen") -> None:
        if mode not in renderer.MODES:
            return
        self._mode = mode
        self._desired_visible = not self._startup_gated and (
            self._persistent_flag or mode != "idle"
        )
        self._invalidate_static_frame()
        self._enqueue_if_started(self._sync_visibility_ui)

    def hide(self) -> None:
        self._desired_visible = False
        self._enqueue_if_started(self._sync_visibility_ui)

    def reassert_z_order(self) -> None:
        if self._startup_gated:
            return
        self._enqueue_if_started(self._raise_ui)

    def release_startup_gate(self) -> bool:
        if not self._startup_gated:
            return False
        self._startup_gated = False
        self._desired_visible = self._persistent_flag or self._mode != "idle"
        self._enqueue_if_started(self._sync_visibility_ui)
        return True

    def set_level(self, level: float) -> None:
        lv = max(0.0, min(1.0, float(level)))
        self._ext_level = lv
        if lv >= AUDIBLE_LEVEL:
            self._last_audible_t = time.perf_counter()

    def set_muted(self, muted: bool) -> None:
        self._muted = bool(muted)
        self._invalidate_static_frame()

    def set_size_scale(self, scale: float) -> None:
        """Live-resize the bar to a new user "Bar size" multiplier (thread-safe).

        The geometry recompute + window resize run on the Qt main thread via the
        UI queue. Width AND height scale together (shape preserved), and the bar
        grows UPWARD from a fixed bottom-centre so it never drops off-screen."""
        self._user_size_scale = renderer.clamp_user_size(scale)
        self._enqueue_if_started(lambda s=self._user_size_scale: self._apply_size_ui(s))

    def set_follow_cursor(self, enabled: bool) -> None:
        """Live-toggle 'follow the mouse to the active monitor'.

        A plain flag the cursor-monitor poll reads each tick: off stops future
        migrations (the bar stays put), on makes the next poll place it on the
        monitor under the mouse. Atomic bool write, like ``set_muted``."""
        self._follow_cursor = bool(enabled)

    def set_on_mute_toggle(self, callback: Callable[[], None] | None) -> None:
        self._on_mute_toggle = callback

    def set_feedback_publisher(self, callback: Callable[[str, dict], None] | None) -> None:
        self._feedback_publisher = callback

    def set_on_show_window(self, callback: Callable[[], None] | None) -> None:
        self._on_show_window = callback

    def set_on_voice_action(self, callback: Callable[[str], None] | None) -> None:
        """Register parent-owned handling for ``"talk"`` and ``"hangup"``.

        The macOS surface lives in a companion process, so importing
        ``runtime_refs.get_speech_pipeline`` here would always return ``None``.
        Keeping the action opaque lets the host forward it to the parent where
        the authoritative SpeechPipeline lives.
        """
        self._on_voice_action = callback

    # The bar has no comment bubble or mouth animation.
    def play_animation(self, name: str, **params: Any) -> None: ...
    def stop_animation(self, name: str) -> None: ...
    def show_listening_transcript(self, text: str = "", duration_ms: int = 30000) -> None: ...
    def hide_comment(self) -> None: ...
    def start_mouth_animation(self, duration_ms: int = 60000) -> None: ...
    def stop_mouth_animation(self) -> None: ...

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def start_in_thread(self, timeout: float = 3.0) -> None:
        """Reject in-process threaded use; the companion must call ``start``.

        Qt/Cocoa windows share AppKit's main-thread rule.  The method exists to
        preserve the surface contract, but deliberately creates no GUI thread.
        """
        del timeout
        log.info(
            "Qt Jarvis Bar requires the companion process main thread; start_in_thread is a no-op."
        )

    def start(self) -> None:
        """Create the transparent window and run the Qt event loop."""
        _prepare_macos_qt_process()
        q = _qt()
        app = q.QtWidgets.QApplication.instance()
        if app is None:
            q.QtGui.QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
                q.Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
            )
            app = q.QtWidgets.QApplication(sys.argv[:1] or ["jarvisbar-qt"])
        app.setQuitOnLastWindowClosed(False)
        self._app = app

        # QApplication must create NSApp first. Only then turn the companion
        # into an accessory process so it owns windows without adding a second
        # Python icon or menu bar. This is the Qt equivalent of the old Tk
        # bootstrap ordering rule, without mixing the two GUI toolkits.
        if sys.platform == "darwin":
            try:
                from AppKit import NSApplication  # type: ignore[import-not-found] # noqa: PLC0415

                NSApplication.sharedApplication().setActivationPolicy_(1)
            except Exception:  # noqa: BLE001 - cosmetic; never block the bar
                log.warning(
                    "Qt Jarvis Bar could not enter macOS accessory mode; "
                    "a companion Dock icon may remain",
                    exc_info=True,
                )

        screen = app.primaryScreen()
        if screen is not None:
            geometry = screen.geometry()
            self._screen_scale = renderer.compute_display_scale(
                geometry.width(), geometry.height()
            )
        renderer.apply_display_scale(self._screen_scale, user_size=self._user_size_scale)
        self._renderer = renderer.JarvisBarRenderer(accent=self._accent)

        window_type = _window_class()
        self._window = window_type(self)
        self._configure_macos_nonactivating_window_ui()
        self._resolve_position_ui()
        self._window.move(self._x, self._y)

        self._running = True
        self._t0 = time.perf_counter()

        self._frame_timer = q.QtCore.QTimer(self._window)
        self._frame_timer.setTimerType(q.Qt.TimerType.PreciseTimer)
        self._frame_timer.setInterval(TARGET_FRAME_MS)
        self._frame_timer.timeout.connect(self._render_frame_ui)
        self._frame_timer.start()

        self._queue_timer = q.QtCore.QTimer(self._window)
        self._queue_timer.setInterval(UI_QUEUE_INTERVAL_MS)
        self._queue_timer.timeout.connect(self._drain_ui_queue)
        self._queue_timer.start()

        self._z_timer = q.QtCore.QTimer(self._window)
        self._z_timer.setInterval(Z_ORDER_GUARD_INTERVAL_MS)
        self._z_timer.timeout.connect(self._z_order_guard_ui)
        self._z_timer.start()

        # A changing alpha mask can make Cocoa emit Leave while the pointer is
        # still inside the fixed bar window. Polling the real global cursor is
        # the authoritative hover signal and also covers non-activating panels
        # that omit Enter/Move notifications.
        self._hover_timer = q.QtCore.QTimer(self._window)
        self._hover_timer.setInterval(HOVER_POLL_INTERVAL_MS)
        self._hover_timer.timeout.connect(self._poll_hover_ui)
        self._hover_timer.start()

        # Follow the mouse across monitors (own timer, off the animation path).
        self._cursor_timer = q.QtCore.QTimer(self._window)
        self._cursor_timer.setInterval(CURSOR_MONITOR_POLL_MS)
        self._cursor_timer.timeout.connect(self._poll_cursor_monitor_ui)
        self._cursor_timer.start()

        # Submit a complete RGBA frame before the first map.  Qt's translucent
        # backing already starts clear, and paintEvent replaces it atomically.
        self._render_frame_ui()
        self._sync_visibility_ui()
        self._started.set()

        if self._stop_requested:
            self._stop_ui()
            return
        try:
            app.exec()
        finally:
            self._running = False

    def stop(self) -> None:
        self._stop_requested = True
        self._running = False
        if self._window is not None:
            self._ui_queue.put(self._stop_ui)

    # ------------------------------------------------------------------
    # Qt-main-thread rendering and commands
    # ------------------------------------------------------------------
    def _enqueue_if_started(self, callback: Callable[[], None]) -> None:
        if self._window is not None:
            self._ui_queue.put(callback)

    def _drain_ui_queue(self) -> None:
        while True:
            try:
                callback = self._ui_queue.get_nowait()
            except queue.Empty:
                return
            try:
                callback()
            except Exception:  # noqa: BLE001 - one command must not kill the loop
                log.exception("Qt Jarvis Bar UI command failed")

    def _render_frame_ui(self) -> None:
        window = self._window
        bar_renderer = self._renderer
        if window is None or bar_renderer is None:
            return
        try:
            now = time.perf_counter()
            effective_mode = renderer.visual_mode(
                self._mode,
                now - self._last_audible_t,
                hold_s=AUDIBLE_HOLD_S,
                # The authoritative audio process forwards level samples over
                # IPC.  Its process-local playback_active flag is unavailable
                # here, so recent real level samples are the truthful signal.
                playback_active=False,
            )
            tick_key = (effective_mode, self._hovered, self._muted)
            if tick_key != self._static_tick_key:
                self._static_tick_key = tick_key
                self._static_tick_count = 0
            else:
                self._static_tick_count += 1
            settled_idle = (
                effective_mode == "idle" and self._static_tick_count >= _IDLE_SETTLE_TICKS
            )
            if settled_idle:
                return

            pil_frame = bar_renderer.render(
                now - self._t0,
                effective_mode,
                self._ext_level,
                hovered=self._hovered,
                muted=self._muted,
            )
            rgba_frame = renderer.key_to_alpha(pil_frame)
            input_mask_key = rgba_frame.getchannel("A").tobytes()
            self._qimage = _qimage_from_pil(rgba_frame)
            # Visual transparency does not imply mouse transparency on macOS:
            # an unmasked Qt window intercepts clicks across its full WIN_W x
            # WIN_H rectangle, including every alpha-zero pixel.  Keep the
            # native window shape synchronized with the eased pill so only the
            # visible bar is interactive and all clear padding clicks through.
            if input_mask_key != self._input_mask_key:
                input_mask = _input_mask_from_frame(self._qimage)
                window.setMask(input_mask)
                self._input_mask = input_mask
                self._input_mask_key = input_mask_key
            window.update()
        except Exception:  # noqa: BLE001 - one frame must never stop the timer
            log.exception("Qt Jarvis Bar frame render failed; dropping one frame")
        finally:
            self._last_frame_ns = time.monotonic_ns()

    def _invalidate_static_frame(self) -> None:
        self._static_tick_key = None
        self._static_tick_count = 0

    def _sync_visibility_ui(self) -> None:
        if self._window is None:
            return
        if self._desired_visible and not self._startup_gated:
            self._do_show_ui()
        else:
            self._set_hovered_ui(False)
            self._window.hide()

    def _do_show_ui(self) -> None:
        if self._window is None or self._startup_gated:
            return
        # Follow mode: a bar popping from hidden should appear on the monitor the
        # mouse is on right now, not wherever it was last shown.
        if self._follow_cursor:
            self._project_onto_cursor_monitor_ui()
        self._reconcile_dynamic_position_ui()
        self._invalidate_static_frame()
        self._render_frame_ui()
        self._window.show()
        self._raise_ui()
        q = _qt()
        mode = self._mode
        q.QtCore.QTimer.singleShot(50, lambda: self._publish_visibility_ui(mode))

    def _raise_ui(self) -> None:
        window = self._window
        if window is None or not window.isVisible() or self._startup_gated:
            return
        if sys.platform == "darwin":
            # QWidget.raise_() activates the Cocoa application even though the
            # QNSPanel cannot become key. The 500 ms Z-order guard then steals
            # foreground status over and over, so clicks in every other app are
            # consumed as activation clicks. Native orderFrontRegardless keeps
            # the panel ordered above apps without activating this process.
            if self._native_window is not None:
                self._native_window.orderFrontRegardless()
            return
        window.raise_()

    def _z_order_guard_ui(self) -> None:
        if self._desired_visible:
            self._reconcile_dynamic_position_ui()
            self._raise_ui()

    def _configure_macos_nonactivating_window_ui(self) -> None:
        """Apply the native non-activating NSPanel contract after Qt starts."""
        if sys.platform != "darwin" or self._window is None:
            return
        # ``winId()`` from Qt's offscreen/minimal test plugins is not an
        # Objective-C NSView pointer. Handing it to pyobjc can spin inside the
        # runtime instead of raising, so native bridging is Cocoa-only.
        platform_name = str(self._app.platformName()).lower() if self._app is not None else ""
        if platform_name and platform_name != "cocoa":
            return
        try:
            from ctypes import c_void_p  # noqa: PLC0415

            import objc  # type: ignore[import-not-found] # noqa: PLC0415
            from AppKit import (  # type: ignore[import-not-found] # noqa: PLC0415
                NSWindowStyleMaskNonactivatingPanel,
            )

            native_view = objc.objc_object(c_void_p=c_void_p(int(self._window.winId())))
            native_window = native_view.window()
            if native_window is None:
                raise RuntimeError("Qt did not expose a native NSWindow")
            _enable_macos_first_mouse(native_view, objc)
            native_window.setStyleMask_(
                int(native_window.styleMask()) | int(NSWindowStyleMaskNonactivatingPanel)
            )
            native_window.setBecomesKeyOnlyIfNeeded_(True)
            native_window.setHidesOnDeactivate_(False)
            # WindowDoesNotAcceptFocus keeps the panel non-activating; it must
            # still request mouse-move delivery so hover controls can react.
            native_window.setAcceptsMouseMovedEvents_(True)
            self._native_window = native_window
        except Exception:  # noqa: BLE001 - topmost is cosmetic; focus safety wins
            # WindowStaysOnTopHint still supplies the normal ordering. Do not
            # fall back to QWidget.raise_() on Darwin: foreground theft is much
            # worse than a bar another app can temporarily cover.
            self._native_window = None
            log.warning(
                "Qt Jarvis Bar could not configure its non-activating macOS panel; "
                "periodic Z-order raises are disabled",
                exc_info=True,
            )

    def _publish_visibility_ui(self, mode: str) -> None:
        publisher = self._feedback_publisher
        window = self._window
        if publisher is None or window is None:
            return
        observed = {
            "viewable": int(window.isVisible()),
            "geometry": (f"{renderer.WIN_W}x{renderer.WIN_H}+{window.x()}+{window.y()}"),
            "x": int(window.x()),
            "y": int(window.y()),
        }
        try:
            publisher(mode, observed)
        except Exception:  # noqa: BLE001 - feedback is diagnostic, never fatal
            log.debug("Qt Jarvis Bar feedback publisher failed", exc_info=True)

    def _stop_ui(self) -> None:
        for timer in (
            self._frame_timer,
            self._queue_timer,
            self._z_timer,
            self._hover_timer,
            self._cursor_timer,
        ):
            if timer is not None:
                timer.stop()
        if self._window is not None:
            self._window.hide()
            self._window.close()
        if self._app is not None:
            self._app.quit()

    # ------------------------------------------------------------------
    # Position, drag, and gestures
    # ------------------------------------------------------------------
    def _set_hovered_ui(self, hovered: bool) -> bool:
        """Apply one stable hover transition and request a fresh frame."""
        hovered = bool(hovered)
        if hovered == self._hovered:
            return False
        self._hovered = hovered
        self._invalidate_static_frame()
        if self._window is not None:
            self._window.update()
        return True

    def _pointer_over_bar_ui(self) -> bool:
        """Read the real cursor, retaining hover across transparent-mask churn.

        Before entry, only the current opaque mask acquires hover so clear
        window padding remains inert. Once acquired, the stable target pill
        footprint becomes the retention area. That lets the pill expand beneath
        the pointer without a mask-generated Leave collapsing it again, while a
        pointer that genuinely leaves the visible bar restores the normal state.
        """
        window = self._window
        if window is None or not window.isVisible():
            return False
        try:
            q = _qt()
            local = window.mapFromGlobal(q.QtGui.QCursor.pos())
            if self._hovered:
                return self._point_in_hover_footprint_ui(local)
            input_mask = self._input_mask
            if input_mask is None:
                return False
            return bool(input_mask.contains(local))
        except Exception:  # noqa: BLE001 - hover is cosmetic and fail-closed
            return False

    def _point_in_hover_footprint_ui(self, point: Any) -> bool:
        """Return whether ``point`` is inside the stable hovered pill bounds."""
        pill_w, pill_h = renderer.target_pill_size(
            self._mode,
            hovered=True,
            muted=self._muted,
        )
        center_x = renderer.WIN_W / 2.0
        center_y = renderer.pill_center_y(float(pill_h))
        slop = HOVER_HIT_SLOP_PX
        x = float(point.x())
        y = float(point.y())
        return (
            center_x - pill_w / 2.0 - slop
            <= x
            < center_x + pill_w / 2.0 + slop
            and center_y - pill_h / 2.0 - slop
            <= y
            < center_y + pill_h / 2.0 + slop
        )

    def _poll_hover_ui(self) -> None:
        if self._startup_gated or not self._desired_visible:
            self._set_hovered_ui(False)
            return
        self._set_hovered_ui(self._pointer_over_bar_ui())

    def _hover_enter_ui(self) -> None:
        self._set_hovered_ui(True)

    def _hover_leave_ui(self) -> None:
        # Never trust a raw Leave generated by replacing the native mask. The
        # cursor poll collapses only after the pointer exits the stable pill.
        self._poll_hover_ui()

    def _geometry_for_screen_ui(
        self,
        screen: Any,
        *,
        respect_visible_dock: bool = True,
    ) -> Any:
        if screen is None:
            return None
        available = screen.availableGeometry()
        if sys.platform != "darwin":
            return available

        full = screen.geometry()
        full_bounds = _geometry_bounds(full)
        available_bounds = _geometry_bounds(available)
        dock_visible = (
            _macos_dock_is_visible_on_screen(full_bounds)
            if respect_visible_dock
            else False
        )
        if dock_visible is not False:
            return available
        expanded = _expand_geometry_for_hidden_dock(full_bounds, available_bounds)
        if expanded == available_bounds:
            return available
        q = _qt()
        return q.QtCore.QRect(*expanded)

    def _primary_geometry_ui(self, *, respect_visible_dock: bool = True) -> Any:
        app = self._app
        if app is None:
            return None
        screen = app.primaryScreen()
        return self._geometry_for_screen_ui(
            screen,
            respect_visible_dock=respect_visible_dock,
        )

    def _screen_geometry_for_point_ui(
        self,
        x: int,
        y: int,
        *,
        respect_visible_dock: bool = True,
    ) -> Any:
        q = _qt()
        app = self._app
        if app is None:
            return None
        screen = app.screenAt(q.QtCore.QPoint(int(x), int(y)))
        if screen is None:
            screen = app.primaryScreen()
        return self._geometry_for_screen_ui(
            screen,
            respect_visible_dock=respect_visible_dock,
        )

    def _clamp_to_geometry_ui(self, x: int, y: int, geometry: Any) -> tuple[int, int]:
        if geometry is None:
            return int(x), int(y)
        local_x, local_y = interaction.clamp_to_screen(
            int(x) - geometry.x(),
            int(y) - geometry.y(),
            screen_w=geometry.width(),
            screen_h=geometry.height(),
            bar_w=renderer.WIN_W,
            bar_h=renderer.WIN_H,
            margin=MARGIN_PX,
        )
        return local_x + geometry.x(), local_y + geometry.y()

    def _default_position_ui(self) -> tuple[int, int]:
        # The preferred location may occupy a currently hidden Dock strip.
        # The visible position is reconciled against the live safe area below.
        geometry = self._primary_geometry_ui(respect_visible_dock=False)
        if geometry is None:
            return interaction.default_bottom_center(
                screen_w=1920,
                screen_h=1080,
                bar_w=renderer.WIN_W,
                bar_h=renderer.WIN_H,
                margin=MARGIN_PX,
            )
        x = geometry.x() + (geometry.width() - renderer.WIN_W) // 2
        y = geometry.y() + geometry.height() - renderer.WIN_H - TASKBAR_GAP_PX
        return self._clamp_to_geometry_ui(x, y, geometry)

    def _resolve_position_ui(self) -> None:
        position: tuple[int, int] | None = None
        rel: tuple[float, float] | None = None
        try:
            from jarvis.core.config_writer import DEFAULT_CONFIG_FILE  # noqa: PLC0415

            position = interaction.load_jarvisbar_position(DEFAULT_CONFIG_FILE)
            rel = interaction.load_jarvisbar_relative(DEFAULT_CONFIG_FILE)
        except Exception:  # noqa: BLE001 - placement degrades to the default
            log.debug("Qt Jarvis Bar position load failed", exc_info=True)

        # Recover a relative spot from a legacy absolute-only config so an
        # upgrade keeps the placement (and reproduces it on the follow monitor).
        if rel is None and position is not None:
            legacy_geometry = self._screen_geometry_for_point_ui(
                position[0] + renderer.WIN_W // 2,
                position[1] + renderer.WIN_H // 2,
                respect_visible_dock=False,
            )
            if legacy_geometry is not None:
                rel = interaction.relative_within(
                    position[0],
                    position[1],
                    work=_geometry_bounds(legacy_geometry),
                    bar_w=renderer.WIN_W,
                    bar_h=renderer.WIN_H,
                )

        # Pick the monitor to place on: follow mode → the monitor under the
        # mouse; otherwise the monitor the saved spot belonged to.
        geometry: Any = None
        if self._follow_cursor:
            geometry = self._cursor_screen_geometry_ui(respect_visible_dock=False)
        if geometry is None:
            if position is None:
                position = self._default_position_ui()
            geometry = self._screen_geometry_for_point_ui(
                position[0] + renderer.WIN_W // 2,
                position[1] + renderer.WIN_H // 2,
                respect_visible_dock=False,
            )

        if geometry is not None and rel is not None:
            work = _geometry_bounds(geometry)
            self._preferred_position = interaction.project_relative(
                rel[0], rel[1], work=work, bar_w=renderer.WIN_W, bar_h=renderer.WIN_H
            )
            self._cur_work = work
            self._rel_pos = rel
        else:
            if position is None:
                position = self._default_position_ui()
            self._preferred_position = self._clamp_to_geometry_ui(
                position[0],
                position[1],
                geometry,
            )
            if geometry is not None:
                self._cur_work = _geometry_bounds(geometry)
                self._rel_pos = interaction.relative_within(
                    self._preferred_position[0],
                    self._preferred_position[1],
                    work=self._cur_work,
                    bar_w=renderer.WIN_W,
                    bar_h=renderer.WIN_H,
                )
        self._x, self._y = self._preferred_position
        self._reconcile_dynamic_position_ui()

    def _apply_size_ui(self, user_scale: float) -> None:
        """Recompute geometry for ``user_scale`` and resize the window (Qt thread).

        Runs on the Qt main thread (enqueued by ``set_size_scale``), serialized
        against the frame timer, so mutating the module geometry globals via
        ``apply_display_scale`` needs no lock.
        """
        window = self._window
        if window is None:
            return
        try:
            old_win_w, old_win_h = renderer.WIN_W, renderer.WIN_H
            old_ref = renderer.OPEN_W or 1
            renderer.apply_display_scale(self._screen_scale, user_size=user_scale)
            # Snap the eased pill so it matches the new window immediately (no
            # clip on shrink, no lag on grow) — see the Tk surface for the
            # rationale.
            r = self._renderer
            if r is not None and old_ref:
                ratio = renderer.OPEN_W / old_ref
                r._st.pw *= ratio  # noqa: SLF001 — same-object render state
                r._st.ph *= ratio  # noqa: SLF001
            # Re-anchor the PREFERRED location by its bottom-centre so the bar
            # grows upward; the Dock-safe reconcile then clamps the actual spot.
            pref = self._preferred_position or (self._x, self._y)
            center_x = pref[0] + old_win_w / 2.0
            bottom_y = pref[1] + old_win_h
            self._preferred_position = (
                round(center_x - renderer.WIN_W / 2.0),
                round(bottom_y - renderer.WIN_H),
            )
            window.setFixedSize(renderer.WIN_W, renderer.WIN_H)
            self._reconcile_dynamic_position_ui()
            # The bar size changed, so the relative-spot basis changed: refresh
            # it so the follow poll keeps the right fraction after a resize.
            self._refresh_rel_from_preferred_ui()
            # Repaint at the new size right away so the input mask + frame track
            # the slider without waiting a frame tick.
            self._invalidate_static_frame()
            self._render_frame_ui()
        except Exception:  # noqa: BLE001 — a resize hiccup must never crash the bar
            log.debug("Qt Jarvis Bar live resize failed", exc_info=True)

    def _reconcile_dynamic_position_ui(self) -> bool:
        """Move between preferred and Dock-safe positions without rewriting config."""
        if self._drag is not None:
            return False
        preferred = self._preferred_position or (self._x, self._y)
        geometry = self._screen_geometry_for_point_ui(
            preferred[0] + renderer.WIN_W // 2,
            preferred[1] + renderer.WIN_H // 2,
        )
        target = self._clamp_to_geometry_ui(preferred[0], preferred[1], geometry)
        if target == (self._x, self._y):
            return False
        self._x, self._y = target
        if self._window is not None:
            self._window.move(self._x, self._y)
        return True

    # ------------------------------------------------------------------
    # Follow the mouse across monitors
    # ------------------------------------------------------------------
    def _cursor_screen_geometry_ui(self, *, respect_visible_dock: bool = True) -> Any:
        """Available geometry (QRect) of the screen under the mouse cursor."""
        q = _qt()
        if self._app is None:
            return None
        try:
            pos = q.QtGui.QCursor.pos()
        except Exception:  # noqa: BLE001 - cursor probe is best-effort
            return None
        return self._screen_geometry_for_point_ui(
            int(pos.x()), int(pos.y()), respect_visible_dock=respect_visible_dock
        )

    def _project_onto_cursor_monitor_ui(self) -> bool:
        """Re-place the bar on the monitor under the mouse (follow mode).

        Projects the bar's RELATIVE spot onto the cursor monitor's available
        geometry and reconciles, so a bigger or smaller monitor keeps the same
        centred/edge placement (:func:`interaction.project_relative`). Returns
        whether the bar moved. Never persists — the relative spot is already the
        saved truth, so migrating monitors changes nothing durable."""
        if self._window is None or not self._follow_cursor or self._drag is not None:
            return False
        geometry = self._cursor_screen_geometry_ui()
        if geometry is None:
            return False
        work = _geometry_bounds(geometry)
        if work == self._cur_work:
            return False  # cursor is on the monitor the bar already lives on
        rel = self._rel_pos
        if rel is None:
            if self._cur_work is None:
                self._cur_work = work
                return False
            base = self._preferred_position or (self._x, self._y)
            rel = interaction.relative_within(
                base[0],
                base[1],
                work=self._cur_work,
                bar_w=renderer.WIN_W,
                bar_h=renderer.WIN_H,
            )
        self._preferred_position = interaction.project_relative(
            rel[0], rel[1], work=work, bar_w=renderer.WIN_W, bar_h=renderer.WIN_H
        )
        self._cur_work = work
        return self._reconcile_dynamic_position_ui()

    def _poll_cursor_monitor_ui(self) -> None:
        """~250 ms follow tick: migrate to the monitor under the mouse."""
        if self._startup_gated or not self._desired_visible or not self._follow_cursor:
            return
        if self._drag is not None:
            return
        self._project_onto_cursor_monitor_ui()

    def _mouse_press_ui(self, event: Any) -> bool:
        q = _qt()
        button = event.button()
        if button == q.Qt.MouseButton.RightButton:
            self._invoke_callback(self._on_show_window, "show-window")
            return True
        if button == q.Qt.MouseButton.MiddleButton:
            self._reset_position_ui()
            return True
        if button != q.Qt.MouseButton.LeftButton or self._window is None:
            return False
        global_pos = event.globalPosition().toPoint()
        self._hovered = True
        self._drag = {
            "sx": int(global_pos.x()),
            "sy": int(global_pos.y()),
            "ox": int(global_pos.x()) - int(self._window.x()),
            "oy": int(global_pos.y()) - int(self._window.y()),
            "cx": float(event.position().x()),
            "hovered": True,
            "moved": False,
        }
        self._invalidate_static_frame()
        return True

    def _mouse_move_ui(self, event: Any) -> bool:
        q = _qt()
        drag = self._drag
        window = self._window
        if drag is None or window is None or not event.buttons() & q.Qt.MouseButton.LeftButton:
            return False
        global_pos = event.globalPosition().toPoint()
        dx = int(global_pos.x()) - drag["sx"]
        dy = int(global_pos.y()) - drag["sy"]
        if not drag["moved"] and not interaction.is_drag(dx, dy, DRAG_THRESHOLD_PX):
            return True
        drag["moved"] = True
        self._x = int(global_pos.x()) - drag["ox"]
        self._y = int(global_pos.y()) - drag["oy"]
        window.move(self._x, self._y)
        return True

    def _mouse_release_ui(self, event: Any) -> bool:
        q = _qt()
        if event.button() != q.Qt.MouseButton.LeftButton:
            return False
        drag = self._drag
        self._drag = None
        if drag is None:
            return True
        if not drag["moved"]:
            self._dispatch_click_ui(float(event.position().x()), hovered=True)
            return True

        preferred_geometry = self._screen_geometry_for_point_ui(
            self._x + renderer.WIN_W // 2,
            self._y + renderer.WIN_H // 2,
            respect_visible_dock=False,
        )
        self._preferred_position = self._clamp_to_geometry_ui(
            self._x,
            self._y,
            preferred_geometry,
        )
        # Pin to the monitor the drop LANDED on and capture its relative spot so
        # the follow poll can reproduce this placement on any other monitor.
        self._refresh_rel_from_preferred_ui()
        self._reconcile_dynamic_position_ui()
        self._persist_position_ui()
        return True

    def _dispatch_click_ui(self, click_x: float, *, hovered: bool | None = None) -> str:
        if time.monotonic() < self._hangup_click_block_until:
            return "none"
        is_hovered = self._hovered if hovered is None else bool(hovered)
        active = self._mode in ("listen", "think", "speak")
        action = interaction.resolve_click(
            click_x,
            renderer.WIN_W,
            self._mode,
            hovered=is_hovered,
            pill_w=renderer.ACTIVE_W if active else None,
        )
        if action == "mute":
            callback = self._on_mute_toggle
            if callback is not None:
                self._invoke_callback(callback, "mute-toggle")
                self._muted = not self._muted
                self._invalidate_static_frame()
        elif action in {"talk", "hangup"}:
            callback = self._on_voice_action
            if callback is None:
                log.warning(
                    "Qt Jarvis Bar %s click has no parent voice-action callback",
                    action,
                )
            else:
                self._invoke_callback(lambda: callback(action), f"voice-{action}")
                if action == "hangup":
                    # Match the Tk surface's optimistic collapse while the
                    # authoritative parent tears the session down (or repairs
                    # a stuck active state). The next bus state reconciles it.
                    self._mode = "idle"
                    self._invalidate_static_frame()
                    self._hangup_click_block_until = time.monotonic() + HANGUP_CLICK_GUARD_S
        return action

    @staticmethod
    def _invoke_callback(callback: Callable[[], None] | None, label: str) -> None:
        if callback is None:
            return
        try:
            callback()
        except Exception:  # noqa: BLE001 - a gesture must not kill the UI loop
            log.warning("Qt Jarvis Bar %s callback failed", label, exc_info=True)

    def _refresh_rel_from_preferred_ui(self) -> None:
        """Recompute ``_rel_pos``/``_cur_work`` from the preferred spot + its screen.

        The monitor-independent relative placement is derived from the current
        preferred position and the available geometry of the screen it sits on.
        Called after a drop, a reset, or a resize — anything that changes the
        absolute spot or the bar size — so the follow poll always projects the
        correct fraction. A missing screen (e.g. offscreen test plugin) leaves
        the previous value untouched."""
        base = self._preferred_position or (self._x, self._y)
        geometry = self._screen_geometry_for_point_ui(
            base[0] + renderer.WIN_W // 2,
            base[1] + renderer.WIN_H // 2,
            respect_visible_dock=False,
        )
        if geometry is None:
            return
        work = _geometry_bounds(geometry)
        self._cur_work = work
        self._rel_pos = interaction.relative_within(
            base[0], base[1], work=work, bar_w=renderer.WIN_W, bar_h=renderer.WIN_H
        )

    def _persist_position_ui(self) -> None:
        try:
            from jarvis.core.config_writer import DEFAULT_CONFIG_FILE  # noqa: PLC0415

            position = self._preferred_position or (self._x, self._y)
            interaction.save_jarvisbar_position(
                DEFAULT_CONFIG_FILE,
                position[0],
                position[1],
                rel=self._rel_pos,
            )
        except Exception:  # noqa: BLE001 - position persistence is non-critical
            log.debug("Qt Jarvis Bar position save failed", exc_info=True)

    def _on_reset_double_click(self, _event: Any = None) -> None:
        del _event
        if self._window is None:
            return
        self._ui_queue.put(self._reset_position_ui)

    def _reset_position_ui(self) -> None:
        # Reset onto the monitor the bar sits on — or, in follow mode, the one
        # under the mouse — not always the primary. Bottom-centre of that screen.
        geometry: Any = None
        if self._follow_cursor:
            geometry = self._cursor_screen_geometry_ui(respect_visible_dock=False)
        if geometry is None:
            base = self._preferred_position or (self._x, self._y)
            geometry = self._screen_geometry_for_point_ui(
                base[0] + renderer.WIN_W // 2,
                base[1] + renderer.WIN_H // 2,
                respect_visible_dock=False,
            )
        if geometry is not None:
            gx, gy, gw, gh = _geometry_bounds(geometry)
            x = gx + (gw - renderer.WIN_W) // 2
            y = gy + gh - renderer.WIN_H - TASKBAR_GAP_PX
            self._preferred_position = self._clamp_to_geometry_ui(x, y, geometry)
        else:
            self._preferred_position = self._default_position_ui()
        self._x, self._y = self._preferred_position
        self._refresh_rel_from_preferred_ui()
        self._reconcile_dynamic_position_ui()
        self._persist_position_ui()


__all__ = [
    "QtJarvisBarOverlay",
    "_dock_reserved_edge",
    "_expand_geometry_for_hidden_dock",
    "_input_mask_from_frame",
    "_macos_dock_is_visible_on_screen",
    "_paint_transparent_frame",
    "_prepare_macos_qt_process",
    "_qimage_from_pil",
]
