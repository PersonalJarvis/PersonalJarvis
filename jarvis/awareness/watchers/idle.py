"""IdleDetector — polls the OS idle-time counter on a 1 s tick.

Plan §5 explicitly permits polling for idle detection (as opposed to the
foreground window, which uses hooks). Rationale: a single idle-seconds
read is an O(1) call with no hook lifecycle. Mouse/KB WinEventHooks (or
their macOS/Linux equivalents) would be a privacy risk (capturing all
input) and have a UAC-proximity aspect.

Idle transition: Active → Idle when ``idle_seconds >= threshold_s``,
Idle → Active on the first input afterwards. Both transitions emit an
event and sync ``manager.state.is_idle`` plus
``current_frame.idle_since_ns`` (via ``dataclasses.replace`` because
FrameSnapshot is frozen).

Per-OS idle-seconds backend, resolved ONCE in ``start()`` (never
re-probed per tick):
  * Windows — ``GetLastInputInfo`` via ctypes. Always available.
  * macOS — Quartz ``CGEventSourceSecondsSinceLastEventType``, lazily
    imported (optional ``pyobjc-framework-Quartz`` extra).
  * Linux — the ``xprintidle`` binary (X11 XScreenSaver extension), only
    when a real X11 display is present; unavailable on Wayland (no
    global idle-time query by OS design) and on headless hosts.

No backend available: ``start()`` logs ONE clear line and returns
WITHOUT starting the tick loop — mirrors ``WindowFocusWatcher``'s
honest degradation, instead of the previous behavior of spinning
forever and reporting "never idle" off Windows.

A backend that starts failing at runtime (binary uninstalled, native
call breaking) disables itself after ``_MAX_CONSECUTIVE_FAILURES``
consecutive failures instead of spamming the log or polling a dead
probe forever.

Lazy imports (AP-26): ``ctypes``, ``Quartz`` and the ``xprintidle``
subprocess call are all imported/spawned only inside their respective
backend methods, never at module scope.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import time
from dataclasses import replace
from typing import TYPE_CHECKING

from jarvis.core.events import IdleEntered, IdleExited
from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS
from jarvis.platform import detect_platform
from jarvis.platform.probes import display_present, is_wayland

if TYPE_CHECKING:
    from jarvis.awareness.manager import AwarenessManager
    from jarvis.core.bus import EventBus

logger = logging.getLogger(__name__)

_TICK_SECONDS: float = 1.0
_STOP_TIMEOUT_S: float = 1.0
_MAX_CONSECUTIVE_FAILURES: int = 5    # runtime probe failures before self-disabling


class IdleDetector:
    """Polls the OS idle-time counter. Emits idle transitions."""

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
        # Resolved once in start() — one of "win32"/"macos"/"linux", or
        # None if start() never ran / no backend is usable on this host.
        self._backend: str | None = None
        self._consecutive_failures: int = 0

    # ---- Lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        """Resolve the idle-seconds backend once, then start the 1 s tick
        loop. Idempotent.

        No usable backend (headless Linux, Wayland, missing xprintidle,
        missing pyobjc-Quartz): logs one clear line and returns without
        starting the loop.
        """
        if self._task is not None:
            return
        backend, reason = self._resolve_backend()
        if backend is None:
            logger.info("idle detection unavailable on this platform: %s", reason)
            return
        self._backend = backend
        self._consecutive_failures = 0
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

    # ---- Backend resolution ---------------------------------------------------

    @staticmethod
    def _resolve_backend() -> tuple[str | None, str]:
        """Pick the idle-seconds backend for this OS, once.

        Returns ``(backend, reason)``. ``backend`` is one of
        ``"win32"``/``"macos"``/``"linux"``, or ``None`` when nothing usable
        was found; ``reason`` is a short human-readable explanation, only
        meaningful when ``backend`` is ``None``.
        """
        plat = detect_platform()
        if plat == "win32":
            return "win32", ""
        if plat == "darwin":
            try:
                import Quartz  # type: ignore[import-not-found] # noqa: PLC0415

                _ = Quartz.CGEventSourceSecondsSinceLastEventType
            except Exception:  # noqa: BLE001
                return None, "pyobjc-framework-Quartz is not installed"
            return "macos", ""
        if plat == "linux":
            if is_wayland():
                return None, (
                    "Wayland has no global idle-time query (X11-only via xprintidle)"
                )
            if not display_present():
                return None, "no graphical display detected (headless session)"
            if shutil.which("xprintidle") is None:
                return None, "xprintidle is not installed (e.g. `apt install xprintidle`)"
            return "linux", ""
        return None, f"unsupported platform ({plat})"

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

        A backend that raises is counted as a failure rather than crashing
        the loop; after ``_MAX_CONSECUTIVE_FAILURES`` in a row it disables
        idle detection for the rest of the session (one log line) instead
        of repeatedly polling a dead probe.
        """
        try:
            idle_seconds = await asyncio.to_thread(self._get_idle_seconds)
        except Exception:  # noqa: BLE001
            self._consecutive_failures += 1
            logger.debug(
                "idle-seconds probe failed (backend=%s)", self._backend, exc_info=True,
            )
            if self._consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
                logger.info(
                    "idle detection: %d consecutive probe failures on backend "
                    "'%s' — disabling idle detection for this session",
                    self._consecutive_failures, self._backend,
                )
                self._stopped = True
            return
        self._consecutive_failures = 0

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

    # ---- Idle-seconds dispatch -------------------------------------------------

    def _get_idle_seconds(self) -> float:
        """Idle seconds via the backend resolved once in ``start()``.

        Dispatches on ``self._backend`` (set by ``start()`` /
        ``_resolve_backend``). Each backend method is best-effort in its
        own right for expected conditions; unexpected failures propagate
        to ``_tick_once``'s failure counter instead of being swallowed
        here, so a persistently broken backend actually disables itself.
        """
        if self._backend == "win32":
            return self._get_idle_seconds_win32()
        if self._backend == "macos":
            return self._get_idle_seconds_macos()
        if self._backend == "linux":
            return self._get_idle_seconds_linux()
        return 0.0

    # ---- Win32 ---------------------------------------------------------------

    @staticmethod
    def _get_idle_seconds_win32() -> float:
        """``GetLastInputInfo`` + ``GetTickCount`` → idle seconds.

        Lazy-imports ``ctypes`` so the module can be imported on Linux/Mac
        without a Win32 stack.

        ``GetTickCount`` returns ms-since-boot and wraps after ~49.7 days
        — the wrap is handled by 64-bit extension when
        ``current_tick < info.dwTime``.
        """
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

    # ---- macOS -----------------------------------------------------------------

    @staticmethod
    def _get_idle_seconds_macos() -> float:
        """``CGEventSourceSecondsSinceLastEventType`` — combined session
        state, any input event type. Lazy ``Quartz`` import (optional
        ``pyobjc-framework-Quartz`` extra).

        Raises if Quartz is unavailable or the native call fails — the
        caller (``_tick_once``) counts this as a probe failure rather than
        silently reporting "never idle".
        """
        import Quartz  # type: ignore[import-not-found] # noqa: PLC0415

        return float(Quartz.CGEventSourceSecondsSinceLastEventType(
            Quartz.kCGEventSourceStateCombinedSessionState,
            Quartz.kCGAnyInputEventType,
        ))

    # ---- Linux -----------------------------------------------------------------

    @staticmethod
    def _get_idle_seconds_linux() -> float:
        """``xprintidle`` prints idle milliseconds on stdout (X11
        XScreenSaver extension). Uses the shared ``NO_WINDOW_CREATIONFLAGS``
        convention (a no-op off Windows).

        Raises on a missing binary, non-zero exit, or timeout — the caller
        counts it as a probe failure rather than silently reporting "never
        idle".
        """
        result = subprocess.run(
            ["xprintidle"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=2.0,
            creationflags=NO_WINDOW_CREATIONFLAGS,
        )
        if result.returncode != 0:
            raise RuntimeError(f"xprintidle exited with code {result.returncode}")
        return int((result.stdout or "").strip()) / 1000.0
