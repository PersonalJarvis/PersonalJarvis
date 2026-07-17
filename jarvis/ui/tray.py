"""System tray icon as the primary UI for Jarvis.

State icons are rendered dynamically via PIL (no .ico assets needed for MVP).
Higher-quality .ico files can later be placed under assets/icons/.

Threading: pystray runs on its own thread (not asyncio-capable).
It communicates with the event loop via a thread-safe queue.
macOS: AppKit forbids that worker thread (main-thread-only UI). A main-thread
start() hosts the icon on the pywebview NSApplication via run_detached();
off-main callers keep the logged no-op (BUG-056). Icon mutations from worker
threads are marshaled to the main thread via PyObjCTools.AppHelper.callAfter.
"""
from __future__ import annotations

import asyncio
import logging
import queue
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from jarvis.platform.probes import display_present

log = logging.getLogger("jarvis.ui.tray")


class JarvisState(str, Enum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    ERROR = "error"
    PAUSED = "paused"


_STATE_COLORS: dict[JarvisState, tuple[int, int, int]] = {
    JarvisState.IDLE: (80, 120, 200),        # calm blue
    JarvisState.LISTENING: (60, 200, 90),    # green
    JarvisState.THINKING: (230, 190, 40),    # yellow
    JarvisState.SPEAKING: (220, 100, 180),   # pink
    JarvisState.ERROR: (220, 60, 60),        # red
    JarvisState.PAUSED: (130, 130, 130),     # gray
}


def _make_icon(state: JarvisState, size: int = 64) -> Any:
    """Returns a PIL image for the tray.

    IDLE uses ``assets/icons/jarvis.ico`` (user branding); active states
    get a colored circle — so the state feedback is preserved while the
    user still sees the real Jarvis logo by default.
    """
    from PIL import Image, ImageDraw  # type: ignore[import-untyped]

    if state == JarvisState.IDLE:
        from jarvis.ui.icon_utils import (
            load_ico_as_pil_image,
            project_icon_path,
        )

        ico = load_ico_as_pil_image(project_icon_path(), size=size)
        if ico is not None:
            return ico

    color = _STATE_COLORS[state]
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((4, 4, size - 4, size - 4), fill=color)
    center = size // 2
    draw.ellipse(
        (center - 6, center - 6, center + 6, center + 6),
        fill=(255, 255, 255, 230),
    )
    return img


@dataclass(slots=True)
class TrayCommand:
    """Action sent from the tray → Jarvis core."""
    action: str  # "quit" | "pause" | "resume" | "reload_config" | "open_settings"
    payload: dict[str, Any] | None = None


class JarvisTray:
    """Tray icon wrapper with status states and a menu.

    Usage:
        tray = JarvisTray(on_command=handle)
        tray.start()              # its own thread
        tray.set_state(JarvisState.LISTENING)
        ...
        tray.stop()
    """

    def __init__(self, on_command: Callable[[TrayCommand], None] | None = None) -> None:
        self._on_command = on_command or (lambda _: None)
        self._state = JarvisState.IDLE
        self._icon: Any = None
        self._thread: threading.Thread | None = None
        self._command_queue: queue.Queue[TrayCommand] = queue.Queue()
        # True when the darwin icon runs detached on the AppKit main thread;
        # icon mutations must then be marshaled via _call_on_main (BUG-056).
        self._darwin_detached = False

    def _build_menu(self) -> Any:
        from pystray import Menu, MenuItem  # type: ignore[import-untyped]

        def _emit(action: str) -> Callable[[Any, Any], None]:
            def _handler(_icon: Any, _item: Any) -> None:
                cmd = TrayCommand(action=action)
                self._command_queue.put(cmd)
                self._on_command(cmd)
                if action == "quit":
                    self.stop()
            return _handler

        return Menu(
            # default=True binds the action to a double-click on the tray icon
            # ("double-click tray -> restore window").
            MenuItem("Open", _emit("open_ui"), default=True),
            Menu.SEPARATOR,
            MenuItem(lambda _: f"Status: {self._state.value}", None, enabled=False),
            Menu.SEPARATOR,
            MenuItem("Pause", _emit("pause"),
                     checked=lambda _: self._state == JarvisState.PAUSED),
            MenuItem("Resume", _emit("resume"),
                     visible=lambda _: self._state == JarvisState.PAUSED),
            MenuItem("Reload config", _emit("reload_config")),
            Menu.SEPARATOR,
            # Phase 5: Emergency stop -> KillRequested(source="tray"); aborts all
            # running Computer-Use tasks and harness dispatches (ADR-0004).
            MenuItem("Emergency stop", _emit("kill")),
            Menu.SEPARATOR,
            MenuItem("Quit", _emit("quit")),
        )

    def _run(self) -> None:
        # Lazy import to keep startup cheap.
        from pystray import Icon  # type: ignore[import-untyped]

        try:
            icon_image = _make_icon(self._state)
            self._icon = Icon(
                "jarvis",
                icon=icon_image,
                title="Jarvis — idle",
                menu=self._build_menu(),
            )
            self._icon.run()
        except Exception:  # noqa: BLE001 — a missing tray host must not die silently (AD-6)
            log.warning(
                "Tray icon could not start (no notification-area / AppIndicator "
                "host?) — continuing without a tray.",
                exc_info=True,
            )
            # The thread exits normally after this; a later start() can re-arm.
            self._icon = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        if sys.platform == "darwin":
            # pystray's darwin backend builds an NSStatusItem in Icon.__init__;
            # AppKit allows UI objects on the MAIN thread only. Created from a
            # worker thread, the first real-Mac boot died with a native AppKit
            # assertion (NSInternalInconsistencyException → process abort,
            # "Python quit unexpectedly") that no try/except can catch
            # (BUG-056). Main-thread callers host the icon detached on the
            # shared NSApplication; off-main callers keep the logged no-op
            # floor (AD-6) — the desktop window + Dock icon remain.
            if threading.current_thread() is not threading.main_thread():
                log.info(
                    "Tray not started: macOS allows status items on the main "
                    "thread only — running without a menu-bar icon."
                )
                return
            if self._darwin_detached and self._icon is not None:
                # Already hosted detached — a second start() must not build a
                # duplicate status item (the thread guard above only covers
                # the worker-thread backends).
                return
            try:
                from AppKit import NSApplication  # type: ignore[import-not-found]
                from pystray import Icon  # type: ignore[import-untyped]

                nsapp = NSApplication.sharedApplication()
                icon_image = _make_icon(self._state)
                self._icon = Icon(
                    "jarvis",
                    icon=icon_image,
                    title="Jarvis — idle",
                    menu=self._build_menu(),
                    darwin_nsapplication=nsapp,
                )
                self._icon.run_detached()
                self._darwin_detached = True
            except Exception:  # noqa: BLE001 — a broken menu-bar host must not crash boot (AD-6)
                log.warning(
                    "Tray not started: macOS menu-bar icon could not be "
                    "created — running without a menu-bar icon.",
                    exc_info=True,
                )
                self._icon = None
                self._darwin_detached = False
            return
        if not display_present():
            # Headless, or a Linux/Wayland session without an AppIndicator /
            # notification-area host: pystray would spawn a daemon thread that
            # dies on the first draw. Degrade to a logged no-op (AD-6) — Jarvis
            # runs fine without a tray; use the desktop window or the web UI.
            log.info(
                "Tray not started: no graphical display / notification-area host "
                "(headless or Wayland without an AppIndicator)."
            )
            return
        self._thread = threading.Thread(target=self._run, name="jarvis-tray", daemon=True)
        self._thread.start()

    def _call_on_main(self, fn: Callable[[], None]) -> None:
        """Runs fn on the AppKit main thread for the detached darwin icon.

        macOS drives the NSStatusItem behind the icon on the main thread only
        (BUG-056), so mutations from worker threads are marshaled through
        PyObjCTools.AppHelper.callAfter. Everywhere else (and as a last
        resort when the marshal itself is unavailable) fn runs directly.
        """
        if not self._darwin_detached:
            fn()
            return
        try:
            from PyObjCTools import AppHelper  # type: ignore[import-not-found]

            AppHelper.callAfter(fn)
        except Exception:  # noqa: BLE001 — AD-6: degrade to a direct call
            fn()

    def stop(self) -> None:
        if self._icon is not None:
            icon = self._icon

            def _do_stop() -> None:
                try:
                    icon.stop()
                except Exception:  # noqa: BLE001
                    pass

            self._call_on_main(_do_stop)
        self._icon = None
        self._darwin_detached = False

    def set_state(self, state: JarvisState) -> None:
        """Thread-safe state update — re-renders the icon and tooltip."""
        if state == self._state:
            return
        self._state = state
        if self._icon is None:
            return
        icon = self._icon

        def _apply() -> None:
            try:
                icon.icon = _make_icon(state)
                icon.title = f"Jarvis — {state.value}"
            except Exception:  # noqa: BLE001
                pass

        self._call_on_main(_apply)

    def set_error(self, message: str) -> None:
        self.set_state(JarvisState.ERROR)
        if self._icon is not None:
            icon = self._icon

            def _apply() -> None:
                try:
                    icon.title = f"Jarvis — Error: {message[:60]}"
                except Exception:  # noqa: BLE001
                    pass

            self._call_on_main(_apply)

    async def command_stream(self) -> asyncio.Queue[TrayCommand]:
        """Async bridge: yields tray commands as an asyncio queue.

        The caller can do `await queue.get()`; a background task reads
        from the thread-safe queue.Queue and forwards them.
        """
        aq: asyncio.Queue[TrayCommand] = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def _forwarder() -> None:
            while True:
                try:
                    cmd = self._command_queue.get(timeout=0.5)
                except queue.Empty:
                    if self._thread is None or not self._thread.is_alive():
                        break
                    continue
                asyncio.run_coroutine_threadsafe(aq.put(cmd), loop)
                if cmd.action == "quit":
                    break

        threading.Thread(target=_forwarder, name="jarvis-tray-bridge", daemon=True).start()
        return aq
