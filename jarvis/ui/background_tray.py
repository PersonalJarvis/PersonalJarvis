"""Tray icon of the background agent service.

The service has no window, so this icon is the user's proof that agents keep
running after the app closed — and the way to stop them or reopen the app.

Two actions only: "Open" starts the desktop app (which then takes over from
the service), "Stop background agents" ends the service. Windows and Linux
desktops get the icon on a pystray worker thread; macOS allows status items on
the main thread only, which the service's event loop owns, and a host without
a display or notification area has nowhere to draw one. Both degrade to a
logged no-op — the service runs the same without the icon.
"""

from __future__ import annotations

import sys
import threading
from collections.abc import Callable
from typing import Any

from loguru import logger


def _icon_image(size: int = 64) -> Any:
    from PIL import Image, ImageDraw  # type: ignore[import-untyped]

    from jarvis.ui.icon_utils import load_ico_as_pil_image, project_icon_path

    image = load_ico_as_pil_image(project_icon_path(), size=size)
    if image is not None:
        return image
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(image).ellipse((4, 4, size - 4, size - 4), fill=(80, 120, 200))
    return image


class BackgroundTray:
    """Owns the pystray icon on its own daemon thread."""

    def __init__(self, *, on_open: Callable[[], None], on_stop: Callable[[], None]) -> None:
        self._on_open = on_open
        self._on_stop = on_stop
        self._icon: Any = None
        self._thread: threading.Thread | None = None

    def start(self) -> bool:
        """Show the icon. ``False`` (and one log line) where none can exist."""
        if sys.platform == "darwin":
            logger.info(
                "background: no menu-bar icon on macOS for the windowless service "
                "(status items need the main thread); open the app to stop it"
            )
            return False
        from jarvis.platform.probes import display_present

        if not display_present():
            logger.info("background: no display / notification area — service runs without a tray")
            return False
        try:
            import pystray  # type: ignore[import-untyped]

            from jarvis.core.branding import PRODUCT_NAME

            menu = pystray.Menu(
                pystray.MenuItem(
                    f"{PRODUCT_NAME}: agents keep running in the background", None, enabled=False
                ),
                pystray.MenuItem(f"Open {PRODUCT_NAME}", self._open, default=True),
                pystray.MenuItem("Stop background agents", self._stop),
            )
            self._icon = pystray.Icon(
                "jarvis-background",
                icon=_icon_image(),
                title=f"{PRODUCT_NAME} — agents running in the background",
                menu=menu,
            )
        except Exception:  # noqa: BLE001 — a tray is a convenience, never a boot blocker
            logger.opt(exception=True).warning("background: tray icon unavailable")
            self._icon = None
            return False
        self._thread = threading.Thread(
            target=self._run, name="jarvis-background-tray", daemon=True
        )
        self._thread.start()
        return True

    def _run(self) -> None:
        icon = self._icon
        if icon is None:
            return
        try:
            icon.run()
        except Exception:  # noqa: BLE001 — the service keeps running without the icon
            logger.opt(exception=True).warning("background: tray icon stopped unexpectedly")

    def _open(self, *_: Any) -> None:
        try:
            self._on_open()
        except Exception:  # noqa: BLE001 — a failed open must not take the tray down
            logger.opt(exception=True).warning("background: opening the app failed")

    def _stop(self, *_: Any) -> None:
        try:
            self._on_stop()
        except Exception:  # noqa: BLE001 — same: report, keep the icon alive
            logger.opt(exception=True).warning("background: stop request failed")

    def stop(self) -> None:
        icon, self._icon = self._icon, None
        if icon is not None:
            try:
                icon.stop()
            except Exception:  # noqa: BLE001 — teardown on the way out
                logger.opt(exception=True).debug("background: tray stop raised")
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)


def open_desktop_app() -> bool:
    """Start the desktop app; it takes over from the running service."""
    try:
        from jarvis.core.background_service import _source_root
        from jarvis.core.instance import current_instance
        from jarvis.ui.relauncher import (
            build_launch_command,
            fresh_user_env,
            restart_workdir,
            spawn_detached,
        )

        # The service itself runs windowless (pythonw on Windows), so its own
        # interpreter is the right one for the window app too.
        argv = build_launch_command(sys.executable)
        cwd = restart_workdir(_source_root())
        spawn_detached(argv, cwd=cwd, env=current_instance().environ(fresh_user_env()))
        return True
    except Exception:  # noqa: BLE001 — reported to the tray caller's log
        logger.opt(exception=True).warning("background: could not start the desktop app")
        return False
