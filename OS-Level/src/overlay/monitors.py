"""Monitor enumeration via Qt + hotplug subscription. Plan §12.3, §12.5.

Qt provides ``screenAdded``/``screenRemoved`` signals; we wrap that in
a small manager so Phase 9.1 can manage the windows-per-screen and
Phase 9.2+ can hook in position recovery (mascot, Plan §13.4).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:  # pragma: no cover
    from PySide6.QtCore import QObject
    from PySide6.QtGui import QScreen


@dataclass(frozen=True)
class MonitorInfo:
    """Snapshot of a monitor, platform-agnostic."""

    name: str
    geometry: tuple[int, int, int, int]  # (x, y, w, h) logical pixels
    device_pixel_ratio: float
    is_primary: bool


def enumerate_monitors() -> list[MonitorInfo]:
    """Snapshot of all live monitors.

    Accesses ``QGuiApplication.screens()`` — a QApplication must
    already exist (see ``main.py``). Test code mocks the
    QGuiApplication module.
    """
    from PySide6.QtGui import QGuiApplication  # lazy: no Qt imports in the self-test

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
    """Subscribes to ``QGuiApplication`` hotplug signals.

    Phase 9.1 only uses ``on_screen_added`` / ``on_screen_removed`` as
    hooks; window lifecycle lives in ``main.setup_windows``.
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
        """Connects hotplug signals. Idempotent."""
        from PySide6.QtGui import QGuiApplication  # lazy

        app = QGuiApplication.instance()
        if app is None:
            raise RuntimeError("MonitorManager.attach: no QGuiApplication")
        if self._app is app:
            return
        if self._on_added is not None:
            app.screenAdded.connect(self._on_added)
        if self._on_removed is not None:
            app.screenRemoved.connect(self._on_removed)
        self._app = app

    def detach(self) -> None:
        """Disconnects signals. Call before app quit, otherwise dangling callbacks."""
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
