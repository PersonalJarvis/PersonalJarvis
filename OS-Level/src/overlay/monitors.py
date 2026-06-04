"""Monitor-Enumeration via Qt + Hotplug-Subscription. Plan §12.3, §12.5.

Qt liefert ``screenAdded``/``screenRemoved``-Signals; wir kapseln das in
einen kleinen Manager, damit Phase 9.1 die Windows-pro-Screen verwaltet
und Phase 9.2+ Position-Recovery (Mascot, Plan §13.4) andocken kann.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:  # pragma: no cover
    from PySide6.QtCore import QObject
    from PySide6.QtGui import QScreen


@dataclass(frozen=True)
class MonitorInfo:
    """Snapshot eines Monitors, plattform-frei."""

    name: str
    geometry: tuple[int, int, int, int]  # (x, y, w, h) logische Pixel
    device_pixel_ratio: float
    is_primary: bool


def enumerate_monitors() -> list[MonitorInfo]:
    """Snapshot aller live Monitore.

    Greift auf ``QGuiApplication.screens()`` zu — vorher muss eine
    QApplication existieren (siehe ``main.py``). Test-Code mockt das
    QGuiApplication-Modul.
    """
    from PySide6.QtGui import QGuiApplication  # lazy: keine Qt-Imports im Self-Test

    app = QGuiApplication.instance()
    if app is None:
        return []
    primary = QGuiApplication.primaryScreen()
    out: list[MonitorInfo] = []
    for screen in QGuiApplication.screens():
        geo = screen.geometry()
        out.append(
            MonitorInfo(
                name=screen.name(),
                geometry=(geo.x(), geo.y(), geo.width(), geo.height()),
                device_pixel_ratio=screen.devicePixelRatio(),
                is_primary=(screen is primary),
            )
        )
    return out


class MonitorManager:
    """Subscribed auf ``QGuiApplication`` Hotplug-Signals.

    Phase 9.1 nutzt nur ``on_screen_added`` / ``on_screen_removed`` als
    Hooks; Window-Lifecycle steckt in ``main.setup_windows``.
    """

    def __init__(
        self,
        on_screen_added: Optional[Callable[["QScreen"], None]] = None,
        on_screen_removed: Optional[Callable[["QScreen"], None]] = None,
    ) -> None:
        self._on_added = on_screen_added
        self._on_removed = on_screen_removed
        self._app: Optional["QObject"] = None

    def attach(self) -> None:
        """Verbindet Hotplug-Signals. Idempotent."""
        from PySide6.QtGui import QGuiApplication  # lazy

        app = QGuiApplication.instance()
        if app is None:
            raise RuntimeError("MonitorManager.attach: keine QGuiApplication")
        if self._app is app:
            return
        if self._on_added is not None:
            app.screenAdded.connect(self._on_added)
        if self._on_removed is not None:
            app.screenRemoved.connect(self._on_removed)
        self._app = app

    def detach(self) -> None:
        """Trennt Signals. Vor App-Quit aufrufen, sonst Dangling-Callbacks."""
        if self._app is None:
            return
        if self._on_added is not None:
            try:
                self._app.screenAdded.disconnect(self._on_added)
            except (RuntimeError, TypeError):  # pragma: no cover
                pass
        if self._on_removed is not None:
            try:
                self._app.screenRemoved.disconnect(self._on_removed)
            except (RuntimeError, TypeError):  # pragma: no cover
                pass
        self._app = None
