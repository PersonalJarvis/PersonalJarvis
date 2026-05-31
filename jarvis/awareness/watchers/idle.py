"""IdleDetector — polls ``GetLastInputInfo`` on a 1 s tick.

Plan §5 explicitly permits polling for idle detection (as opposed to the
foreground window, which uses hooks). Rationale: ``GetLastInputInfo`` is
an O(1) ctypes call with no hook lifecycle. Mouse/KB WinEventHooks would
be a privacy risk (capturing all input) and have a UAC-proximity aspect.

Idle transition: Active → Idle when ``idle_seconds >= threshold_s``,
Idle → Active on the first input afterwards. Both transitions emit an
event and sync ``manager.state.is_idle`` plus
``current_frame.idle_since_ns`` (via ``dataclasses.replace`` because
FrameSnapshot is frozen).

Lazy imports: ``ctypes`` is only loaded inside ``_get_idle_seconds``.
On non-Windows the detector is functionally a no-op (returns 0 idle).
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import replace
from typing import TYPE_CHECKING

from jarvis.core.events import IdleEntered, IdleExited

if TYPE_CHECKING:
    from jarvis.awareness.manager import AwarenessManager
    from jarvis.core.bus import EventBus

logger = logging.getLogger(__name__)

_TICK_SECONDS: float = 1.0
_STOP_TIMEOUT_S: float = 1.0


class IdleDetector:
    """Polls ``GetLastInputInfo``. Emits idle transitions."""

    def __init__(
        self,
        *,
        manager: AwarenessManager,
        bus: EventBus,
        threshold_s: int = 300,    # Plan D-A6: 5 min default
    ) -> None:
        self._manager = manager
        self._bus = bus
        self._threshold_s = threshold_s
        self._task: asyncio.Task[None] | None = None
        self._stopped: bool = False
        self._is_idle: bool = False
        self._became_idle_at_ns: int = 0

    # ---- Lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        """Start the 1 s tick loop. Idempotent."""
        if self._task is not None:
            return
        self._stopped = False
        loop = asyncio.get_running_loop()
        self._task = loop.create_task(self._run(), name="awareness-idle")

    async def stop(self) -> None:
        """Cancel the task, wait <1 s. Idempotent."""
        self._stopped = True
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=_STOP_TIMEOUT_S)
        except (TimeoutError, asyncio.CancelledError):
            pass
        except Exception:  # noqa: BLE001
            logger.debug("IdleDetector task ended with exception", exc_info=True)

    # ---- Tick-Loop -----------------------------------------------------------

    async def _run(self) -> None:
        """Loop until ``_stopped`` or cancelled. Each tick: ``_tick_once`` + sleep."""
        while not self._stopped:
            try:
                await self._tick_once()
            except asyncio.CancelledError:
                break
            except Exception:  # noqa: BLE001
                # Defensive: a single failed tick must not tear down the
                # loop. Log the error once and keep ticking.
                logger.debug("IdleDetector tick failed", exc_info=True)
            try:
                await asyncio.sleep(_TICK_SECONDS)
            except asyncio.CancelledError:
                break

    async def _tick_once(self) -> None:
        """One tick iteration: measure idle time, check transition, publish event.

        Independently testable — tests call ``_tick_once()`` directly,
        without the ``_run()`` loop and therefore without waiting 1 s.
        """
        try:
            idle_seconds = await asyncio.to_thread(self._get_idle_seconds)
        except Exception:  # noqa: BLE001
            logger.debug("GetLastInputInfo failed", exc_info=True)
            return

        now_ns = time.time_ns()
        should_be_idle = idle_seconds >= self._threshold_s

        if should_be_idle and not self._is_idle:
            # Active → Idle
            self._is_idle = True
            self._became_idle_at_ns = now_ns - int(idle_seconds * 1e9)
            self._manager.state.is_idle = True
            cur = self._manager.state.current_frame
            if cur is not None:
                # FrameSnapshot is frozen — create a new one with replace().
                self._manager.state.current_frame = replace(
                    cur, idle_since_ns=self._became_idle_at_ns,
                )
            await self._bus.publish(IdleEntered(idle_since_ns=self._became_idle_at_ns))

        elif not should_be_idle and self._is_idle:
            # Idle → Active
            was_idle_for_ms = max(0, int((now_ns - self._became_idle_at_ns) / 1_000_000))
            self._is_idle = False
            self._became_idle_at_ns = 0
            self._manager.state.is_idle = False
            cur = self._manager.state.current_frame
            if cur is not None and cur.idle_since_ns is not None:
                self._manager.state.current_frame = replace(cur, idle_since_ns=None)
            await self._bus.publish(IdleExited(was_idle_for_ms=was_idle_for_ms))

    # ---- Win32 ---------------------------------------------------------------

    @staticmethod
    def _get_idle_seconds() -> float:
        """``GetLastInputInfo`` + ``GetTickCount`` → idle seconds.

        Lazy-imports ``ctypes`` so the module can be imported on Linux/Mac
        without a Win32 stack. On non-Windows: returns 0.0.

        ``GetTickCount`` returns ms-since-boot and wraps after ~49.7 days
        — the wrap is handled by 64-bit extension when
        ``current_tick < info.dwTime``.
        """
        if os.name != "nt":
            return 0.0
        import ctypes  # noqa: PLC0415
        from ctypes import wintypes  # noqa: PLC0415

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.UINT),
                ("dwTime", wintypes.DWORD),
            ]

        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(LASTINPUTINFO)
        ok = ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info))
        if not ok:
            return 0.0
        current_tick = ctypes.windll.kernel32.GetTickCount()
        if current_tick < info.dwTime:
            current_tick += 0x100000000
        idle_ms = current_tick - info.dwTime
        return idle_ms / 1000.0
