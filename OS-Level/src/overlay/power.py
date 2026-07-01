"""GetSystemPowerStatus polling. Plan §17.3 throttling strategy.

AC vs battery -> halve the FPS. Polling every 30 s on a daemon thread.

SYSTEM_POWER_STATUS struct (winbase.h):

    BYTE  ACLineStatus            // 0=offline, 1=online, 255=unknown
    BYTE  BatteryFlag             // 0..8 bit-mask
    BYTE  BatteryLifePercent      // 0..100 or 255 (unknown)
    BYTE  SystemStatusFlag        // 1 = battery saver on
    DWORD BatteryLifeTime         // seconds until empty or 0xFFFFFFFF
    DWORD BatteryFullLifeTime     // ditto

We primarily read ACLineStatus + SystemStatusFlag.
"""

from __future__ import annotations

import logging
import sys
import threading
from dataclasses import dataclass
from typing import Callable, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PowerStatus:
    on_battery: bool
    battery_saver: bool
    battery_percent: Optional[int]  # None if unknown


PowerCallback = Callable[[PowerStatus], None]


def query_status() -> Optional[PowerStatus]:
    """Plan §17.3 — GetSystemPowerStatus via ctypes.

    Returns None on non-Windows or on an API error.
    """
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    class _SystemPowerStatus(ctypes.Structure):
        _fields_ = [
            ("ACLineStatus", wintypes.BYTE),
            ("BatteryFlag", wintypes.BYTE),
            ("BatteryLifePercent", wintypes.BYTE),
            ("SystemStatusFlag", wintypes.BYTE),
            ("BatteryLifeTime", wintypes.DWORD),
            ("BatteryFullLifeTime", wintypes.DWORD),
        ]

    try:
        kernel = ctypes.windll.kernel32
    except (OSError, AttributeError):  # pragma: no cover
        return None

    kernel.GetSystemPowerStatus.argtypes = [ctypes.POINTER(_SystemPowerStatus)]
    kernel.GetSystemPowerStatus.restype = wintypes.BOOL

    sps = _SystemPowerStatus()
    ok = kernel.GetSystemPowerStatus(ctypes.byref(sps))
    if not ok:
        logger.debug("GetSystemPowerStatus failed")
        return None

    ac = sps.ACLineStatus
    on_battery = ac == 0
    battery_saver = bool(sps.SystemStatusFlag & 0x01)
    pct = sps.BatteryLifePercent
    battery_percent: Optional[int] = None if pct == 255 else int(pct)
    return PowerStatus(
        on_battery=on_battery,
        battery_saver=battery_saver,
        battery_percent=battery_percent,
    )


class PowerMonitor:
    """30-second polling thread for SYSTEM_POWER_STATUS.

    Lifecycle::

        mon = PowerMonitor(callback=on_change)
        mon.start()
        ...
        mon.stop()
    """

    def __init__(
        self,
        *,
        callback: Optional[PowerCallback] = None,
        poll_interval_s: float = 30.0,
        query_fn: Callable[[], Optional[PowerStatus]] = query_status,
    ) -> None:
        self._callback = callback
        self._poll_interval = poll_interval_s
        self._query_fn = query_fn
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_status: Optional[PowerStatus] = None

    @property
    def last_status(self) -> Optional[PowerStatus]:
        return self._last_status

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def set_callback(self, callback: Optional[PowerCallback]) -> None:
        self._callback = callback

    def poll_once(self) -> Optional[PowerStatus]:
        """Sync Poll. Returnt PowerStatus oder None."""
        status = self._query_fn()
        if status is None:
            return None
        prev = self._last_status
        self._last_status = status
        if self._callback is not None and (
            prev is None
            or prev.on_battery is not status.on_battery
            or prev.battery_saver is not status.battery_saver
        ):
            try:
                self._callback(status)
            except Exception:  # noqa: BLE001
                logger.exception("PowerMonitor callback raised")
        return status

    def start(self) -> None:
        if self.is_running:
            return
        self._stop.clear()
        # Poll immediately on start so the caller doesn't have to wait
        # 30 s for the first status.
        self.poll_once()
        self._thread = threading.Thread(
            target=self._run, name="overlay-power-monitor", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def _run(self) -> None:
        while not self._stop.is_set():
            if self._stop.wait(timeout=self._poll_interval):
                return
            self.poll_once()


__all__ = [
    "PowerCallback",
    "PowerMonitor",
    "PowerStatus",
    "query_status",
]
