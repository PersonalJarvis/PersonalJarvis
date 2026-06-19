"""System-Tray-Icon als primäre UI für Jarvis.

State-Icons werden dynamisch per PIL gerendert (keine .ico-Assets nötig für MVP).
Später können hochwertige .ico-Files unter assets/icons/ abgelegt werden.

Threading: pystray läuft in einem eigenen Thread (nicht asyncio-fähig).
Kommunikation mit dem Event-Loop über einen thread-safe Queue.
"""
from __future__ import annotations

import asyncio
import logging
import queue
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
    JarvisState.IDLE: (80, 120, 200),        # ruhiges Blau
    JarvisState.LISTENING: (60, 200, 90),    # Grün
    JarvisState.THINKING: (230, 190, 40),    # Gelb
    JarvisState.SPEAKING: (220, 100, 180),   # Pink
    JarvisState.ERROR: (220, 60, 60),        # Rot
    JarvisState.PAUSED: (130, 130, 130),     # Grau
}


def _make_icon(state: JarvisState, size: int = 64) -> Any:
    """Liefert PIL-Image für den Tray.

    IDLE nutzt ``assets/icons/jarvis.ico`` (User-Branding), aktive Zustände
    bekommen einen farbigen Kreis — so bleibt die State-Rückmeldung erhalten
    und der User sieht trotzdem im Default das echte Jarvis-Logo.
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
    """Vom Tray → Jarvis-Core geschickte Action."""
    action: str  # "quit" | "pause" | "resume" | "reload_config" | "open_settings"
    payload: dict[str, Any] | None = None


class JarvisTray:
    """Tray-Icon-Wrapper mit Status-States und Menu.

    Usage:
        tray = JarvisTray(on_command=handle)
        tray.start()              # eigener Thread
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

    def stop(self) -> None:
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:  # noqa: BLE001
                pass
        self._icon = None

    def set_state(self, state: JarvisState) -> None:
        """Thread-safe State-Update — rerendert Icon und Tooltip."""
        if state == self._state:
            return
        self._state = state
        if self._icon is None:
            return
        try:
            self._icon.icon = _make_icon(state)
            self._icon.title = f"Jarvis — {state.value}"
        except Exception:  # noqa: BLE001
            pass

    def set_error(self, message: str) -> None:
        self.set_state(JarvisState.ERROR)
        if self._icon is not None:
            try:
                self._icon.title = f"Jarvis — Error: {message[:60]}"
            except Exception:  # noqa: BLE001
                pass

    async def command_stream(self) -> asyncio.Queue[TrayCommand]:
        """Async-Bridge: yieldet Tray-Commands als asyncio-Queue.

        Der Aufrufer kann `await queue.get()` machen; Ein Background-Task liest
        aus dem thread-safen queue.Queue und forwarded.
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
