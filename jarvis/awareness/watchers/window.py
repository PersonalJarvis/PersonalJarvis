"""WindowFocusWatcher — Win32 ``SetWinEventHook`` on ``EVENT_SYSTEM_FOREGROUND``.

Architecture (binding):

  1. Dedicated pump thread runs ``MsgWaitForMultipleObjects(stop_event,
     QS_ALLINPUT)``. Wakeup on event OR new Win32 message. Subagent
     recommendation Q1 — replaces ``pythoncom.PumpMessages`` because it
     can be shut down deterministically via ``win32event.SetEvent``.
  2. Hook callback (Win32 thread) does ONLY
     ``loop.call_soon_threadsafe(_safe_enqueue, payload)``. NO logging,
     NO await, NO PrivacyFilter, NO bus.publish (Plan §5+§10
     Hard-Negative HN4).
  3. Drain loop (asyncio) pulls ``(timestamp_ns, hwnd)`` from the queue,
     calls ``_resolve_window_meta`` (in ``asyncio.to_thread``) +
     ``PrivacyFilter`` + ``bus.publish`` of ``FrameUpdated`` or
     ``AwarenessCaptureBlocked``.

Platform guard: ``os.name != "nt"`` makes ``start()`` a no-op (tests
run without a Win32 stack). Lazy imports of ``ctypes``, ``win32event``
and ``psutil`` INSIDE the methods (Plan §5 Hard-Negative HN3).

Lifecycle order in ``stop()`` (subagent Q4, 6 phases):
  P1: cancel drain task (asyncio side first, so no ``bus.publish``
      fires after we unregister the hook).
  P2: ``win32event.SetEvent(stop_event)`` — wakes ``MsgWaitForMultipleObjects``.
  P3: ``pump_thread.join(timeout=1.5)``.
  P4: defensive ``UnhookWinEvent`` in case the pump thread crashed; the
      primary cleanup path is in the pump thread's finally block.
  P5: ``CloseHandle(stop_event)``.
  P6: remaining queue items are GC'd (loss acceptable — Plan §5 does not
      require no-event-loss on shutdown).
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from typing import TYPE_CHECKING

from jarvis.awareness.state import FrameSnapshot
from jarvis.core.events import AwarenessCaptureBlocked, FrameUpdated
from jarvis.core.win32_dpi import ensure_dpi_awareness

if TYPE_CHECKING:
    from jarvis.awareness.manager import AwarenessManager
    from jarvis.awareness.privacy import PrivacyFilter
    from jarvis.core.bus import EventBus

logger = logging.getLogger(__name__)

# Win32 constants — defined as module-level constants so the module can
# be imported on Linux. Values are platform-stable (Win32 API contract).
_EVENT_SYSTEM_FOREGROUND: int = 0x0003
_WINEVENT_OUTOFCONTEXT: int = 0x0000
_WINEVENT_SKIPOWNPROCESS: int = 0x0002

_QUEUE_MAX: int = 64                  # Burst-Buffer (Alt+Tab-Marathon)
_DRAIN_GET_TIMEOUT_S: float = 0.25    # Drain-Wakeup um stop-Flag zu pruefen
_PUMP_JOIN_TIMEOUT_S: float = 1.5     # Stop-Phase 3
_DRAIN_CANCEL_TIMEOUT_S: float = 0.5  # Stop-Phase 1
_HWND_DEDUPE_NS: int = 50_000_000     # 50ms — schluckt Win32-Doppel-Events
_PUMP_READY_TIMEOUT_S: float = 2.0    # start() wait-bis-Hook-gesetzt


class WindowFocusWatcher:
    """Responds to foreground window changes via a Win32 hook."""

    def __init__(
        self,
        *,
        manager: AwarenessManager,
        privacy: PrivacyFilter,
        bus: EventBus,
    ) -> None:
        self._manager = manager
        self._privacy = privacy
        self._bus = bus

        # Async-Side
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue[tuple[int, int]] = asyncio.Queue(maxsize=_QUEUE_MAX)
        self._drain_task: asyncio.Task[None] | None = None
        self._drain_stop: asyncio.Event = asyncio.Event()

        # Sync-Side (Win32)
        self._pump_thread: threading.Thread | None = None
        self._hook_handle: int | None = None
        self._stop_event_handle: int | None = None
        # The WINEVENTPROC instance MUST be kept alive — otherwise it is
        # GC'd, the C pointer dangles, and Win32 calls invalid memory.
        self._wineventproc_ref: object | None = None

        # State
        self._started: bool = False
        self._stopping: bool = False
        # Drops split by source thread to eliminate the +=1 race
        # (CPython GIL switch between read/modify/write).
        self._drops_pump: int = 0     # written in the Win32 _proc callback
        self._drops_async: int = 0    # written in the asyncio _safe_enqueue
        self._last_hwnd: int = 0
        self._last_emit_ns: int = 0

    @property
    def _drops(self) -> int:
        """Sum of both drop counters (compat for smoke tests + unit tests)."""
        return self._drops_pump + self._drops_async

    # ---- Lifecycle ----------------------------------------------------------

    async def start(self) -> None:
        """Start pump thread and drain task. Idempotent.

        On Linux/Mac: no-op (tests run without Win32).
        """
        if self._started:
            return
        if os.name != "nt":
            self._started = True
            return

        ensure_dpi_awareness()
        self._loop = asyncio.get_running_loop()
        self._drain_stop.clear()
        self._stop_event_handle = self._create_stop_event()

        ready = threading.Event()
        thread = threading.Thread(
            target=self._pump_loop,
            name="awareness-window-pump",
            args=(ready,),
            daemon=True,
        )
        thread.start()
        if not ready.wait(timeout=_PUMP_READY_TIMEOUT_S):
            logger.warning(
                "WindowFocusWatcher: pump-thread did not signal ready in %.1fs",
                _PUMP_READY_TIMEOUT_S,
            )

        self._pump_thread = thread
        self._drain_task = self._loop.create_task(
            self._drain_loop(), name="awareness-window-drain",
        )
        self._started = True

    async def stop(self) -> None:
        """Clean shutdown <2 s. Idempotent. 6-phase sequence."""
        if self._stopping:
            return
        if not self._started:
            return
        self._stopping = True

        try:
            # P1: Drain-Task cancellen (asyncio-side)
            self._drain_stop.set()
            drain_task = self._drain_task
            self._drain_task = None
            if drain_task is not None:
                drain_task.cancel()
                try:
                    await asyncio.wait_for(drain_task, timeout=_DRAIN_CANCEL_TIMEOUT_S)
                except (TimeoutError, asyncio.CancelledError):
                    pass
                except Exception:  # noqa: BLE001
                    logger.debug("drain task ended with exception", exc_info=True)

            # P2: SetEvent (Win32) — wakes MsgWaitForMultipleObjects immediately
            if self._stop_event_handle is not None:
                try:
                    import win32event  # noqa: PLC0415

                    win32event.SetEvent(self._stop_event_handle)
                except Exception:  # noqa: BLE001, S110
                    # Defensive Win32 cleanup — do not escalate errors,
                    # otherwise the next phase step in stop() would hang.
                    pass

            # P3: Pump-Thread join
            pump = self._pump_thread
            self._pump_thread = None
            if pump is not None and pump.is_alive():
                pump.join(timeout=_PUMP_JOIN_TIMEOUT_S)
                if pump.is_alive():
                    logger.warning(
                        "WindowFocusWatcher pump-thread did not exit in %.1fs",
                        _PUMP_JOIN_TIMEOUT_S,
                    )

            # P4: Defensive UnhookWinEvent (primary path is in pump-loop finally)
            if self._hook_handle is not None:
                try:
                    import ctypes  # noqa: PLC0415

                    # Own WinDLL instance + argtypes — otherwise ctypes
                    # silently truncates the 64-bit handle to c_int (32-bit).
                    u32 = ctypes.WinDLL("user32", use_last_error=True)
                    u32.UnhookWinEvent.argtypes = [ctypes.c_void_p]
                    u32.UnhookWinEvent.restype = ctypes.c_int
                    u32.UnhookWinEvent(self._hook_handle)
                except Exception:  # noqa: BLE001, S110
                    # Defensive Win32 cleanup — do not escalate errors,
                    # otherwise the next phase step in stop() would hang.
                    pass
                self._hook_handle = None

            # P5: CloseHandle stop_event
            if self._stop_event_handle is not None:
                try:
                    import win32api  # noqa: PLC0415

                    win32api.CloseHandle(self._stop_event_handle)
                except Exception:  # noqa: BLE001, S110
                    # Defensive Win32 cleanup — do not escalate errors,
                    # otherwise the next phase step in stop() would hang.
                    pass
                self._stop_event_handle = None

            # P6: leftover queue items get GC'd. Log the drops counter.
            if self._drops > 0:
                logger.info(
                    "WindowFocusWatcher: %d frames dropped (pump=%d, async=%d)",
                    self._drops, self._drops_pump, self._drops_async,
                )
        finally:
            self._started = False
            self._stopping = False

    # ---- Win32-Pump-Thread --------------------------------------------------

    @staticmethod
    def _create_stop_event() -> int:
        """Create a manual-reset event handle. Lazy-imports win32event."""
        import win32event  # noqa: PLC0415

        # CreateEvent(SecurityAttrs=None, ManualReset=True, InitialState=False, Name=None)
        return win32event.CreateEvent(None, True, False, None)

    def _pump_loop(self, ready: threading.Event) -> None:
        """Win32 message pump using ``MsgWaitForMultipleObjects``.

        Lifecycle:
          1. SetWinEventHook for EVENT_SYSTEM_FOREGROUND.
          2. ``ready.set()`` → start() may return.
          3. Wait loop until stop_event is signalled.
          4. PeekMessage drain on each wakeup.
          5. ``UnhookWinEvent`` in finally — on the same thread that set
             it (best practice, even though OUTOFCONTEXT hooks technically
             allow it from any thread).
        """
        import ctypes  # noqa: PLC0415
        from ctypes import wintypes  # noqa: PLC0415

        import win32event  # noqa: PLC0415

        # WinEventProc signature per Win32 documentation.
        WINEVENTPROC = ctypes.WINFUNCTYPE(
            None,
            ctypes.c_void_p,    # HWINEVENTHOOK
            wintypes.DWORD,     # event
            wintypes.HWND,      # hwnd
            wintypes.LONG,      # idObject
            wintypes.LONG,      # idChild
            wintypes.DWORD,     # idEventThread
            wintypes.DWORD,     # dwmsEventTime
        )

        def _proc(hook, event, hwnd, idObject, idChild, idThread, dwmsEventTime):
            # Hard-Negative HN4: NO logging, NO await, NO PrivacyFilter
            # in this callback. Enqueue only.
            # We filter on the actual window (idObject==OBJID_WINDOW (0)).
            if idObject != 0 or idChild != 0:
                return
            if not hwnd:
                return
            payload = (time.time_ns(), int(hwnd))
            loop = self._loop
            if loop is None or loop.is_closed():
                return
            try:
                loop.call_soon_threadsafe(self._safe_enqueue, payload)
            except RuntimeError:
                # Loop closed mid-call — drop. Race-Fenster <1ms.
                self._drops_pump += 1

        proc_ref = WINEVENTPROC(_proc)
        # MUST be kept alive — otherwise GC'd and Win32 calls invalid memory.
        self._wineventproc_ref = proc_ref

        # WinDLL("user32", use_last_error=True) instead of ctypes.windll.user32:
        # (a) own instance so our argtypes settings do not affect other modules
        #     (vision/screenshot.py etc.),
        # (b) use_last_error=True is required for ctypes.get_last_error() below
        #     — otherwise the call always returns 0 instead of the real Win32 error.
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.SetWinEventHook.restype = ctypes.c_void_p
        user32.SetWinEventHook.argtypes = [
            wintypes.DWORD,      # eventMin
            wintypes.DWORD,      # eventMax
            ctypes.c_void_p,     # hmodWinEventProc (HMODULE — pointer-sized)
            WINEVENTPROC,        # pfnWinEventProc — explicit Callback-Type
            wintypes.DWORD,      # idProcess
            wintypes.DWORD,      # idThread
            wintypes.DWORD,      # dwFlags
        ]
        user32.UnhookWinEvent.argtypes = [ctypes.c_void_p]
        user32.UnhookWinEvent.restype = ctypes.c_int
        hook = user32.SetWinEventHook(
            _EVENT_SYSTEM_FOREGROUND,
            _EVENT_SYSTEM_FOREGROUND,
            None,
            proc_ref,
            0,    # all processes
            0,    # all threads
            _WINEVENT_OUTOFCONTEXT | _WINEVENT_SKIPOWNPROCESS,
        )
        if not hook:
            err = ctypes.get_last_error()
            logger.error("SetWinEventHook failed (last_error=%d) — Watcher disabled", err)
            ready.set()
            return

        self._hook_handle = int(hook)
        ready.set()

        # MsgWaitForMultipleObjects-Loop
        QS_ALLINPUT = 0x04FF
        WAIT_OBJECT_0 = 0x00000000
        INFINITE = 0xFFFFFFFF
        PM_REMOVE = 0x0001

        msg = wintypes.MSG()
        try:
            while True:
                rc = win32event.MsgWaitForMultipleObjects(
                    [self._stop_event_handle], False, INFINITE, QS_ALLINPUT,
                )
                if rc == WAIT_OBJECT_0:
                    break    # stop signalled
                # rc == WAIT_OBJECT_0 + 1 → messages are pending
                while user32.PeekMessageW(ctypes.byref(msg), 0, 0, 0, PM_REMOVE):
                    user32.TranslateMessage(ctypes.byref(msg))
                    user32.DispatchMessageW(ctypes.byref(msg))
        except Exception:  # noqa: BLE001
            logger.exception("WindowFocusWatcher pump-loop crashed")
        finally:
            # Unregister hook — on the SAME thread that registered it.
            if self._hook_handle is not None:
                try:
                    user32.UnhookWinEvent(self._hook_handle)
                except Exception:  # noqa: BLE001, S110
                    # Defensive Win32 cleanup — do not escalate errors,
                    # otherwise the next phase step in stop() would hang.
                    pass
                self._hook_handle = None
            self._wineventproc_ref = None

    # ---- Async-Side ---------------------------------------------------------

    def _safe_enqueue(self, payload: tuple[int, int]) -> None:
        """Runs in the asyncio loop. Drop-on-full without raising an exception."""
        try:
            self._queue.put_nowait(payload)
        except asyncio.QueueFull:
            self._drops_async += 1

    async def _drain_loop(self) -> None:
        """Consume the queue, calling ``_drain_once`` per item until stopped."""
        while not self._drain_stop.is_set():
            try:
                await self._drain_once()
            except asyncio.CancelledError:
                break
            except Exception:  # noqa: BLE001
                logger.debug("drain iteration failed", exc_info=True)

    async def _drain_once(self) -> None:
        """One drain iteration: 1 item from queue (or timeout) → bus.

        Independently testable: tests call ``_drain_once()`` directly after
        ``_safe_enqueue()`` without starting ``_drain_loop``.
        """
        try:
            payload = await asyncio.wait_for(
                self._queue.get(), timeout=_DRAIN_GET_TIMEOUT_S,
            )
        except TimeoutError:
            return

        ts_ns, hwnd = payload

        # Dedupe: Win32 sometimes emits 2-3 EVENT_SYSTEM_FOREGROUND events
        # within <50 ms during Alt+Tab. We filter here instead of in the
        # callback (HN4).
        if hwnd == self._last_hwnd and (ts_ns - self._last_emit_ns) < _HWND_DEDUPE_NS:
            return

        # Retrieve window title + PID + process name from hwnd — ctypes
        # calls + psutil are blocking, so run in to_thread.
        try:
            window_title, pid, process_name = await asyncio.to_thread(
                self._resolve_window_meta, hwnd,
            )
        except Exception:  # noqa: BLE001
            logger.debug("Window-meta lookup failed for hwnd=%d", hwnd, exc_info=True)
            return

        # PrivacyFilter — on the asyncio thread, not the Win32 thread.
        allowed, reason = self._privacy.is_allowed(
            window_title=window_title,
            process_name=process_name,
        )

        # Phase A5: probes only for allowed frames (privacy + cost guard).
        # probe_all has a 200 ms hard cap and does not propagate errors.
        probe_data: dict[str, object] = {}
        if allowed and pid > 0:
            try:
                probe_data = await self._manager.probe_all(
                    pid=pid, process_name=process_name,
                )
            except Exception:    # noqa: BLE001
                logger.debug("probe_all failed for pid=%d", pid, exc_info=True)
                probe_data = {}

        # Build FrameSnapshot and set AwarenessState.current_frame.
        # Single-writer pattern: only this drain loop writes
        # current_frame. Readers are synchronous in the same loop, no race.
        cur_frame = self._manager.state.current_frame
        idle_since_ns = cur_frame.idle_since_ns if cur_frame is not None else None
        snap = FrameSnapshot(
            timestamp_ns=ts_ns,
            active_window_title=window_title,
            active_process_name=process_name,
            active_pid=pid,
            is_capture_allowed=allowed,
            git_branch=probe_data.get("git_branch"),    # type: ignore[arg-type]
            open_file_hint=probe_data.get("open_file_hint"),    # type: ignore[arg-type]
            idle_since_ns=idle_since_ns,
        )
        self._manager.state.current_frame = snap
        self._last_hwnd = hwnd
        self._last_emit_ns = ts_ns

        # Publish bus event. allowed → FrameUpdated, blocked →
        # AwarenessCaptureBlocked (NOT both).
        if allowed:
            await self._bus.publish(FrameUpdated(
                window_title=window_title,
                process_name=process_name,
                pid=pid,
                is_capture_allowed=True,
            ))
        else:
            await self._bus.publish(AwarenessCaptureBlocked(
                window_title=window_title,
                process_name=process_name,
                reason=reason,
            ))

    @staticmethod
    def _resolve_window_meta(hwnd: int) -> tuple[str, int, str]:
        """``GetWindowTextW`` + ``GetWindowThreadProcessId`` + psutil.name().

        Returns ``(window_title, pid, process_name)``. On any error:
        ``("", 0, "")``. Lazy imports.
        """
        if os.name != "nt":
            return ("", 0, "")
        import ctypes  # noqa: PLC0415
        from ctypes import wintypes  # noqa: PLC0415

        try:
            user32 = ctypes.windll.user32
            length = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value or ""

            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            pid_int = int(pid.value)

            try:
                import psutil  # noqa: PLC0415

                proc_name = psutil.Process(pid_int).name()
            except Exception:  # noqa: BLE001
                proc_name = ""

            return (title, pid_int, proc_name)
        except Exception:  # noqa: BLE001
            return ("", 0, "")
