"""pywebview-Shell-Orchestrator.

**Thread-Choreografie** (siehe Plan §5.1, §5.2):

- Main-Thread:        `webview.start()` — blockt bis Window-Close
- Daemon-Thread 1:    Uvicorn (WebServer)
- Daemon-Thread 2:    pystray (JarvisTray) — Doppelklick-Handler emittiert
                      ``TrayCommand("open_ui")`` in die Queue
- Daemon-Thread 3:    Tray→Shell-Bridge (diese Klasse) — konsumiert die Queue
                      thread-safely und ruft `request_show()` via
                      `webview.windows[0].show()` auf

**Warum Close-Button = hide statt destroy?** pywebview's Default-Handler
beendet bei Close den Main-Loop. User-Entscheidung (2026-04-20): Close =
Minimize-to-Tray. `on_closing`-Callback gibt `False` zurück → Window wird
versteckt statt zerstört.
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
    """Kapselt pywebview-Fenster + Tray-Bridge + Shutdown-Hook."""

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
        """Holt das Fenster nach vorne — thread-safe callable."""
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
        """Erstellt das Window und startet pywebview — blockt Main-Thread."""
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

        # Close-Button = hide (Minimize-to-Tray-Verhalten)
        def _on_closing() -> bool:
            # Wenn wir wirklich beenden wollen (via Tray-Menu "Beenden"), wurde
            # `_close_requested = True` gesetzt → echtes Close zulassen.
            if self._close_requested:
                return True
            self.request_hide()
            return False

        self._window.events.closing += _on_closing

        # Token ins Frontend injecten, sobald das DOM geladen ist.
        def _on_loaded() -> None:
            try:
                self._window.evaluate_js(
                    f"window.__JARVIS_TOKEN={self._token!r};"
                    "window.dispatchEvent(new Event('jarvis-token-ready'));"
                )
            except Exception as exc:  # noqa: BLE001
                logger.opt(exception=exc).warning("Token-Injection failed")

        self._window.events.loaded += _on_loaded

        # Tray→Shell Bridge starten, falls Tray vorhanden
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
        """Wirkliches Beenden — wird vom Tray-Menu "Beenden" aufgerufen."""
        self._close_requested = True
        if self._window is not None:
            try:
                self._window.destroy()
            except Exception as exc:  # noqa: BLE001
                logger.opt(exception=exc).warning("Window.destroy() failed")

    def _tray_bridge_loop(self) -> None:
        """Pollt die Tray-Command-Queue und reagiert auf open_ui/quit.

        **Wichtig:** wir rufen `show()`/`hide()` **aus diesem Thread**, nicht
        aus dem pystray-Thread direkt — pystray-Callbacks dürfen nicht in
        pywebview-APIs reingreifen (WinForms-Deadlock-Risiko).
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
