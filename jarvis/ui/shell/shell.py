"""pywebview shell orchestrator.

**Thread choreography** (see Plan §5.1, §5.2):

- Main thread:        `webview.start()` — blocks until window close
- Daemon thread 1:    Uvicorn (web server)
- Daemon thread 2:    pystray (JarvisTray) — the double-click handler emits
                      ``TrayCommand("open_ui")`` into the queue
- Daemon thread 3:    Tray→shell bridge (this class) — consumes the queue
                      thread-safely and calls `request_show()` via
                      `webview.windows[0].show()`

**Why does the close button hide instead of destroy?** pywebview's default
handler ends the main loop on close. User decision (2026-04-20): close =
minimize-to-tray. The `on_closing` callback returns `False` → the window is
hidden instead of destroyed.
"""
from __future__ import annotations

import threading
from collections.abc import Callable
from typing import TYPE_CHECKING

from loguru import logger

from .window import WindowConfig

if TYPE_CHECKING:
    from jarvis.ui.tray import JarvisTray


class JarvisShell:
    """Encapsulates the pywebview window + tray bridge + shutdown hook."""

    def __init__(
        self,
        window_config: WindowConfig,
        *,
        token: str,
        tray: JarvisTray | None = None,
        on_close: Callable[[], None] | None = None,
        debug: bool = False,
    ) -> None:
        self._cfg = window_config
        self._token = token
        self._tray = tray
        self._on_close = on_close or (lambda: None)
        self._debug = debug
        self._window = None
        self._running = False
        self._close_requested = False

    @property
    def window(self) -> object:
        return self._window

    def request_show(self) -> None:
        """Brings the window to the front — thread-safe callable."""
        if self._window is None:
            return
        try:
            self._window.show()
            self._window.restore()
        except Exception as exc:  # noqa: BLE001
            logger.opt(exception=exc).warning("Window.show() failed")

    def request_hide(self) -> None:
        if self._window is not None:
            try:
                self._window.hide()
            except Exception as exc:  # noqa: BLE001
                logger.opt(exception=exc).warning("Window.hide() failed")

    def run(self) -> None:
        """Creates the window and starts pywebview — blocks the main thread."""
        import webview  # type: ignore[import-untyped]

        self._window = webview.create_window(
            title=self._cfg.title,
            url=self._cfg.url,
            width=self._cfg.width,
            height=self._cfg.height,
            min_size=(self._cfg.min_width, self._cfg.min_height),
            background_color=self._cfg.background_color,
            hidden=self._cfg.start_hidden,
            frameless=self._cfg.frameless,
            easy_drag=self._cfg.easy_drag,
            confirm_close=self._cfg.confirm_close,
        )

        # Close button = hide (minimize-to-tray behavior)
        def _on_closing() -> bool:
            # If we really want to quit (via the tray menu "Quit"),
            # `_close_requested = True` was set → allow a real close.
            if self._close_requested:
                return True
            self.request_hide()
            return False

        self._window.events.closing += _on_closing

        # Inject the token into the frontend as soon as the DOM has loaded.
        def _on_loaded() -> None:
            try:
                self._window.evaluate_js(
                    f"window.__JARVIS_TOKEN={self._token!r};"
                    "window.dispatchEvent(new Event('jarvis-token-ready'));"
                )
            except Exception as exc:  # noqa: BLE001
                logger.opt(exception=exc).warning("Token-Injection failed")

        self._window.events.loaded += _on_loaded

        # Start the tray→shell bridge, if a tray exists
        if self._tray is not None:
            bridge = threading.Thread(
                target=self._tray_bridge_loop,
                name="jarvis-tray-shell-bridge",
                daemon=True,
            )
            bridge.start()

        self._running = True
        webview.start(debug=self._debug)
        self._running = False
        self._on_close()

    def quit(self) -> None:
        """Actual quit — called from the tray menu "Quit"."""
        self._close_requested = True
        if self._window is not None:
            try:
                self._window.destroy()
            except Exception as exc:  # noqa: BLE001
                logger.opt(exception=exc).warning("Window.destroy() failed")

    def _tray_bridge_loop(self) -> None:
        """Polls the tray command queue and reacts to open_ui/quit.

        **Important:** we call `show()`/`hide()` **from this thread**, not
        directly from the pystray thread — pystray callbacks must not reach
        into pywebview APIs (WinForms deadlock risk).
        """
        assert self._tray is not None
        import queue

        while self._running or self._tray is not None:
            try:
                cmd = self._tray._command_queue.get(timeout=0.5)  # noqa: SLF001
            except queue.Empty:
                continue
            if cmd.action == "open_ui":
                self.request_show()
            elif cmd.action == "quit":
                self.quit()
                break
