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

Platform guard: ``detect_platform() != "win32"`` routes ``start()`` to the
POSIX polling fallback below instead of the Win32 hook path. Lazy imports
of ``ctypes``, ``win32event`` and ``psutil`` INSIDE the methods (Plan §5
Hard-Negative HN3).

POSIX polling fallback (macOS/Linux): no OS-level foreground-change hook
is used there, so ``WindowFocusWatcher`` instead polls
``jarvis.platform.window_state.foreground_window()`` on a
``_POSIX_POLL_INTERVAL_S`` cadence and calls the SAME ``_emit_frame`` tail
the Win32 drain loop uses, so both platforms publish identical
``FrameUpdated`` / ``AwarenessCaptureBlocked`` events. macOS always has a
display (no TCC/Accessibility grant is needed just to read the frontmost
application — ``window_state.foreground_window()`` already degrades to
``None`` without the optional Screen-Recording grant or without pyobjc).
An empty probe only disables the fallback while NO probe has ever
succeeded — that is a backend which cannot work here; afterwards it means
"nothing is focused right now", which on X11 is an everyday state (see
``_poll_loop``). A window that merely has no readable TITLE is a healthy
probe too and never counts against it. Headless Linux and Wayland sessions
are gated up front (mirrors ``IdleDetector``): one honest log line, no
polling task, no crash.

Lifecycle order in ``stop()`` (subagent Q4, 6 phases):
  P1: cancel drain task (asyncio side first, so no ``bus.publish``
      fires after we unregister the hook).
  P2: ``win32event.SetEvent(stop_event)`` — wakes ``MsgWaitForMultipleObjects``.
  P3: ``pump_thread.join(timeout=1.5)``.
  P4: defensive ``UnhookWinEvent`` in case the pump thread crashed; the
      primary cleanup path is in the pump thread's finally block.
  P5: ``CloseHandle(stop_event)``.
  P6: remaining queue items are DRAINED and the per-run change-detection
      state is reset (loss acceptable — Plan §5 does not require
      no-event-loss on shutdown — but carrying either into a later
      ``start()`` is not: the queue and the "last seen window" markers live
      on the instance, so a restart would replay pre-stop frames and then
      swallow the first genuine one as "unchanged").
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import threading
import time
from typing import TYPE_CHECKING

from jarvis.awareness.state import FrameSnapshot
from jarvis.core.events import AwarenessCaptureBlocked, FrameUpdated
from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS
from jarvis.core.win32_dpi import ensure_dpi_awareness
from jarvis.platform import detect_platform, window_state
from jarvis.platform.probes import display_present, is_wayland

if TYPE_CHECKING:
    from jarvis.awareness.manager import AwarenessManager
    from jarvis.awareness.privacy import PrivacyFilter
    from jarvis.core.bus import EventBus
    from jarvis.platform.window_state import WindowInfo

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

# POSIX polling fallback (macOS/Linux) — no OS-level focus-change hook is
# used there, so we poll at a modest cadence instead. 2 s balances staying
# off the voice hot path against noticing an app switch reasonably fast.
_POSIX_POLL_INTERVAL_S: float = 2.0
_POSIX_POLL_MAX_FAILURES: int = 5     # empty probes BEFORE the first success
_POSIX_POLL_STOP_TIMEOUT_S: float = 1.5
# Last-resort pid lookup for an X11 window (see ``_linux_window_pid``). Kept
# generous on purpose: a slow answer is still a correct one, while a timeout
# yields an empty process name, and the privacy rules that key on the process
# (rather than the title) then have nothing to match against.
_POSIX_PID_PROBE_TIMEOUT_S: float = 5.0


def _linux_window_pid(window_id: int) -> int:
    """Owning pid of an X11 window id via ``xdotool``, or ``0``. Never raises.

    A LAST RESORT: ``window_state.foreground_window`` already reads the pid off
    the very window it took the title from, so this only runs for a client that
    advertises no ``_NET_WM_PID`` at all — which is also the property xdotool
    reads, so the honest expectation is that it returns 0 as well. It stays as
    the single documented seam for that lookup instead of being inlined, and it
    no longer routes the routine "this window has no pid property" case through
    a broad exception handler that logged it as a resolution failure with a
    traceback, drowning the real ones.

    Runs on a worker thread (called via ``asyncio.to_thread``), so it must be
    bounded: every ``subprocess`` failure mode — a missing binary, a dead X
    connection, a hung server — degrades to ``0``.
    """
    executable = shutil.which("xdotool")
    if executable is None:
        return 0
    try:
        proc = subprocess.run(
            [executable, "getwindowpid", str(window_id)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_POSIX_PID_PROBE_TIMEOUT_S,
            check=False,
            creationflags=NO_WINDOW_CREATIONFLAGS,
        )
    except (OSError, subprocess.SubprocessError):
        logger.debug(
            "xdotool getwindowpid failed for window %s", window_id, exc_info=True,
        )
        return 0
    if proc.returncode != 0:
        logger.debug("xdotool reports no pid for X11 window %s", window_id)
        return 0
    raw = (proc.stdout or "").strip()
    try:
        pid = int(raw)
    except ValueError:
        logger.debug(
            "xdotool returned a non-numeric pid %r for X11 window %s", raw, window_id,
        )
        return 0
    return pid if pid > 0 else 0


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
        # Guards the read-and-clear of _hook_handle: the pump thread and stop()
        # both release the hook, from different threads. See _take_hook_handle.
        self._hook_lock: threading.Lock = threading.Lock()
        self._stop_event_handle: int | None = None
        # The WINEVENTPROC instance MUST be kept alive — otherwise it is
        # GC'd, the C pointer dangles, and Win32 calls invalid memory.
        self._wineventproc_ref: object | None = None

        # POSIX polling side (macOS/Linux fallback — no hook available)
        self._poll_task: asyncio.Task[None] | None = None
        self._poll_stop: asyncio.Event = asyncio.Event()
        self._poll_last_handle: int | None = None
        self._poll_last_title: str = ""

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
        """Start the Win32 pump thread and drain task, or the POSIX polling
        fallback on macOS/Linux. Idempotent.

        Headless Linux / Wayland: logs one honest line and starts no task
        (mirrors ``IdleDetector``).
        """
        if self._started:
            return
        if detect_platform() != "win32":
            self._start_posix_polling()
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
        """Clean shutdown <2 s. Idempotent. 6-phase sequence on Windows;
        a 2-phase cancel-and-join on the POSIX polling fallback."""
        if self._stopping:
            return
        if not self._started:
            return
        self._stopping = True

        if detect_platform() != "win32":
            try:
                await self._stop_posix_polling()
                self._reset_frame_state()
            finally:
                self._started = False
                self._stopping = False
            return

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
            hook_handle = self._take_hook_handle()
            if hook_handle is not None:
                try:
                    import ctypes  # noqa: PLC0415

                    # Own WinDLL instance + argtypes — otherwise ctypes
                    # silently truncates the 64-bit handle to c_int (32-bit).
                    u32 = ctypes.WinDLL("user32", use_last_error=True)
                    u32.UnhookWinEvent.argtypes = [ctypes.c_void_p]
                    u32.UnhookWinEvent.restype = ctypes.c_int
                    u32.UnhookWinEvent(hook_handle)
                except Exception:  # noqa: BLE001, S110
                    # Defensive Win32 cleanup — do not escalate errors,
                    # otherwise the next phase step in stop() would hang.
                    pass

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

            # P6: discard leftover queue items. They must NOT survive into a
            # later start(): the queue lives on the instance, so a restart
            # (awareness toggled off and on again) used to drain events
            # captured before the stop and publish them as fresh frames with
            # their old timestamps.
            if self._drops > 0:
                logger.info(
                    "WindowFocusWatcher: %d frames dropped (pump=%d, async=%d)",
                    self._drops, self._drops_pump, self._drops_async,
                )
            self._reset_frame_state()
        finally:
            self._started = False
            self._stopping = False

    def _take_hook_handle(self) -> int | None:
        """Atomically claim the hook handle so exactly ONE caller unhooks it.

        Both ``stop()``'s defensive P4 and the pump thread's ``finally`` release
        the hook, and P3's join is explicitly allowed to TIME OUT — so a pump
        thread that is merely slow can be inside its cleanup while ``stop()``
        reads the very same handle. Calling ``UnhookWinEvent`` twice on one
        handle has a use-after-free shape: the second call either no-ops or,
        once Win32 has recycled that handle value for a hook installed
        elsewhere in the process, tears down SOMEONE ELSE's hook. Read-and-clear
        under a lock makes the loser of that race see ``None`` and do nothing.
        """
        with self._hook_lock:
            handle, self._hook_handle = self._hook_handle, None
            return handle

    def _reset_frame_state(self) -> None:
        """Drop queued frames and per-run change-detection state.

        Both survive on the instance across a stop/start cycle, and both are
        wrong on the next run: the queued payloads describe windows from the
        previous session, and the stale "last seen" markers make the FIRST
        probe after a restart compare equal to what was focused before the
        stop — so the POSIX poller emitted nothing at all until the user
        happened to switch windows, leaving ``state.current_frame`` frozen.
        """
        while True:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                # Not a failure: an empty queue is exactly the drained state
                # this loop exists to reach.
                break
        self._last_hwnd = 0
        self._last_emit_ns = 0
        self._poll_last_handle = None
        self._poll_last_title = ""

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
        MSG_PENDING = WAIT_OBJECT_0 + 1
        INFINITE = 0xFFFFFFFF
        PM_REMOVE = 0x0001

        msg = wintypes.MSG()
        try:
            while True:
                rc = win32event.MsgWaitForMultipleObjects(
                    [self._stop_event_handle], False, INFINITE, QS_ALLINPUT,
                )
                # Only WAIT_OBJECT_0 + 1 means "messages are pending". Anything
                # else is either the stop event (WAIT_OBJECT_0) or a wait that
                # failed — WAIT_FAILED (0xFFFFFFFF) or WAIT_ABANDONED_0 on a
                # closed/invalid stop handle. Falling through to PeekMessage on
                # a failed wait spun this thread at 100 % CPU forever, because
                # nothing in the loop can clear the failure and the wait returns
                # immediately every time. Treat any non-message result as
                # terminal and leave (same doctrine as AP-20 for socket loops):
                # stop() still joins us, and the finally block unhooks.
                if rc != MSG_PENDING:
                    if rc != WAIT_OBJECT_0:
                        logger.error(
                            "WindowFocusWatcher: MsgWaitForMultipleObjects "
                            "returned 0x%08X (last_error=%d) — pump stopped, "
                            "focus tracking is off until the next start()",
                            int(rc) & 0xFFFFFFFF,
                            ctypes.get_last_error(),
                        )
                    break
                while user32.PeekMessageW(ctypes.byref(msg), 0, 0, 0, PM_REMOVE):
                    user32.TranslateMessage(ctypes.byref(msg))
                    user32.DispatchMessageW(ctypes.byref(msg))
        except Exception:  # noqa: BLE001
            logger.exception("WindowFocusWatcher pump-loop crashed")
        finally:
            # Unregister hook — on the SAME thread that registered it. Claimed
            # atomically so a stop() whose join timed out cannot unhook the same
            # handle a second time (see _take_hook_handle).
            handle = self._take_hook_handle()
            if handle is not None:
                try:
                    user32.UnhookWinEvent(handle)
                except Exception:  # noqa: BLE001, S110
                    # Defensive Win32 cleanup — do not escalate errors,
                    # otherwise the next phase step in stop() would hang.
                    pass
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
        #
        # The lower bound matters: these timestamps come from time.time_ns(),
        # a WALL clock that can step BACKWARDS (an NTP correction, the user
        # fixing the clock, a VM resuming from suspend). A negative delta is
        # also "< 50 ms", so a single backward step used to swallow every
        # further event for the window that happened to be focused across it —
        # focus tracking silently froze on that app until the clock caught up.
        # A backward jump is not a duplicate, so it must not dedupe.
        since_last_ns = ts_ns - self._last_emit_ns
        if hwnd == self._last_hwnd and 0 <= since_last_ns < _HWND_DEDUPE_NS:
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

        if not window_title.strip() and not process_name.strip():
            # Nothing at all could be read about this window. _resolve_window_meta
            # answers ("", 0, "") both for a genuine probe failure and for a
            # window whose owner exited mid-lookup, and that is NOT a frame:
            # publishing it handed PrivacyFilter two empty strings, which match
            # no block pattern and therefore fall through to
            # "default_allow_for_unknown" — so an unidentifiable window was
            # waved through AND overwrote state.current_frame, blanking the
            # foreground context the brain reads until the next app switch.
            # The POSIX poller in this same class already refuses exactly this
            # ("the privacy filter must never be handed an empty title to wave
            # through"); the Win32 path never got the same guard.
            logger.debug(
                "Win32 window meta unresolvable for hwnd=%d — probe discarded, "
                "frame not published", hwnd,
            )
            return

        await self._emit_frame(
            ts_ns=ts_ns, handle=hwnd, window_title=window_title,
            pid=pid, process_name=process_name,
        )

    async def _emit_frame(
        self, *, ts_ns: int, handle: int, window_title: str, pid: int, process_name: str,
    ) -> None:
        """Privacy-filter + probe + publish one resolved frame.

        Shared tail of both frame sources — the Win32 drain loop (hwnd
        events dequeued from the WinEventHook callback) and the POSIX
        polling fallback (macOS/Linux, see below) — so every platform
        builds and publishes the identical ``FrameUpdated`` /
        ``AwarenessCaptureBlocked`` event through one code path. Callers
        are responsible for their own change-detection/dedupe before
        calling this (the Win32 path dedupes by hwnd+timestamp in
        ``_drain_once``; the POSIX path dedupes by handle+title in
        ``_poll_once``).
        """
        # PrivacyFilter — on the asyncio thread, not a native callback.
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
        # Single-writer pattern: only this method writes current_frame
        # (the Win32 drain loop and the POSIX poll loop never run at the
        # same time — start() branches to exactly one of them). Readers
        # are synchronous in the same loop, no race.
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
        self._last_hwnd = handle
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
            # Own WinDLL instance + explicit argtypes, exactly like the
            # UnhookWinEvent call in stop(): with the shared ``ctypes.windll``
            # and no signatures, the HWND is marshalled as a 32-bit c_int, so a
            # handle above 0x7FFFFFFF raises ArgumentError (frame silently
            # dropped) instead of resolving — and binding signatures on the
            # PROCESS-WIDE windll would leak into vision/screenshot.py and
            # window_state.py, which configure the same functions themselves.
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
            user32.GetWindowTextLengthW.restype = ctypes.c_int
            user32.GetWindowTextW.argtypes = [
                wintypes.HWND, wintypes.LPWSTR, ctypes.c_int,
            ]
            user32.GetWindowTextW.restype = ctypes.c_int
            user32.GetWindowThreadProcessId.argtypes = [
                wintypes.HWND, ctypes.POINTER(wintypes.DWORD),
            ]
            user32.GetWindowThreadProcessId.restype = wintypes.DWORD

            handle = wintypes.HWND(hwnd)
            length = max(0, int(user32.GetWindowTextLengthW(handle)))
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(handle, buf, length + 1)
            title = buf.value or ""

            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(handle, ctypes.byref(pid))
            pid_int = int(pid.value)

            try:
                import psutil  # noqa: PLC0415

                proc_name = psutil.Process(pid_int).name()
            except Exception:  # noqa: BLE001
                proc_name = ""

            return (title, pid_int, proc_name)
        except Exception:  # noqa: BLE001
            return ("", 0, "")

    # ---- POSIX polling fallback (macOS/Linux) --------------------------------

    def _start_posix_polling(self) -> None:
        """Start the polling fallback, or degrade honestly.

        macOS always has a display (``probes.display_present()`` is
        unconditional there) and needs no permission just to read the
        frontmost application, so it always starts polling; a session
        that genuinely cannot be read (no pyobjc, or later a revoked
        grant) is discovered per-tick by ``_poll_once`` and disables the
        loop via the consecutive-failure counter instead of being gated
        here. Linux needs a real X11 session — headless hosts and Wayland
        compositors (no global foreground-window query by OS design, the
        same reason ``probes.has_hotkey``/``has_cursor`` refuse there) are
        rejected up front instead of polling a backend that can never work.
        """
        plat = detect_platform()
        if plat not in ("darwin", "linux"):
            logger.info(
                "window-focus tracking unavailable on this platform (%s)", plat,
            )
            return
        if plat == "linux" and (is_wayland() or not display_present()):
            logger.info(
                "window-focus tracking unavailable on this platform: "
                "no usable X11 display (headless session or Wayland)",
            )
            return

        self._poll_stop.clear()
        loop = asyncio.get_running_loop()
        self._poll_task = loop.create_task(
            self._poll_loop(), name="awareness-window-poll",
        )

    async def _stop_posix_polling(self) -> None:
        """Cancel the polling task, wait <1.5 s. Idempotent."""
        self._poll_stop.set()
        task = self._poll_task
        self._poll_task = None
        if task is None:
            return
        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=_POSIX_POLL_STOP_TIMEOUT_S)
        except (TimeoutError, asyncio.CancelledError):
            pass
        except Exception:  # noqa: BLE001
            logger.debug("POSIX poll task ended with exception", exc_info=True)

    async def _poll_loop(self) -> None:
        """Call ``_poll_once`` every ``_POSIX_POLL_INTERVAL_S`` until stopped.

        The failure counter is a STARTUP GATE, not a running health check: it
        only disables the loop while no probe has EVER returned a usable window,
        which is the signature of a backend that cannot work here (no pyobjc, no
        xdotool, a ``DISPLAY`` pointing at an X server that is not actually
        reachable). Once one window has been read, an empty probe is treated as a
        healthy "nothing is focused right now" observation and the loop keeps
        going.

        That distinction is the whole point. ``xdotool getactivewindow`` exits
        non-zero — and the probe therefore reports nothing — whenever no client
        window holds the X11 input focus: the user is on the desktop or the
        wallpaper, a window just closed, a lock/screensaver is up, or a
        pointer-focus WM briefly parked focus on the root window. All of those
        are ordinary, transient states. Counting them meant 10 s on the desktop
        permanently killed focus tracking for the rest of the session and blamed
        a missing xdotool in the log. This is the same conflation of "the backend
        is broken" with "there is momentarily nothing to report" that
        ``_poll_once`` already had to unlearn for title-less X11 windows.

        Waits on ``_poll_stop`` rather than a plain sleep so ``stop()`` wakes the
        loop immediately instead of waiting out the interval.
        """
        consecutive_failures = 0
        ever_succeeded = False
        while not self._poll_stop.is_set():
            try:
                ok = await self._poll_once()
            except asyncio.CancelledError:
                break
            except Exception:  # noqa: BLE001
                # Mirrors the Win32 drain loop: one bad iteration must not end
                # focus tracking for the session. Without this the task died
                # with an unretrieved exception and no log line at all — the
                # feature was simply off, silently.
                logger.debug("POSIX poll iteration failed", exc_info=True)
                ok = False

            if ok:
                ever_succeeded = True
                consecutive_failures = 0
            elif not ever_succeeded:
                consecutive_failures += 1
                if consecutive_failures >= _POSIX_POLL_MAX_FAILURES:
                    logger.info(
                        "window-focus polling never produced usable window info "
                        "(%d attempts) — stopping: no backend that can read the "
                        "foreground window here (missing pyobjc/xdotool, or the "
                        "advertised display cannot be queried)",
                        consecutive_failures,
                    )
                    return

            try:
                await asyncio.wait_for(
                    self._poll_stop.wait(), timeout=_POSIX_POLL_INTERVAL_S,
                )
            except TimeoutError:
                pass
            except asyncio.CancelledError:
                break

    async def _poll_once(self) -> bool:
        """One polling iteration: probe the foreground window, emit a frame
        via :meth:`_emit_frame` if focus actually changed.

        Returns ``True`` when the probe itself worked (whether or not focus
        changed — an unchanged focus is still a healthy probe), ``False``
        only when the backend answered with nothing at all (feeds the
        consecutive-failure counter in :meth:`_poll_loop`).
        Independently testable — tests call this directly instead of
        waiting out the real polling interval.
        """
        try:
            win = await asyncio.to_thread(self._posix_foreground_window)
        except Exception:  # noqa: BLE001
            logger.debug("POSIX foreground-window probe failed", exc_info=True)
            return False

        if win is None:
            return False

        if not (win.title or "").strip():
            # A window WITHOUT a readable title is not a broken backend — X11
            # windows legitimately carry no name, and xdotool's getwindowname
            # can fail for one window while getactivewindow keeps working.
            # Counting it as a failure meant five such polls (10 s) permanently
            # disabled focus tracking for the whole session, with no way back
            # short of a restart. Report the probe as healthy, but publish
            # nothing: a frame with no title is unidentifiable, and the privacy
            # filter must never be handed an empty title to wave through.
            logger.debug(
                "POSIX foreground window has no readable title (handle=%s) — "
                "probe healthy, frame not published",
                win.handle,
            )
            return True

        changed = (win.handle, win.title) != (self._poll_last_handle, self._poll_last_title)
        if not changed:
            return True

        try:
            pid, process_name = await asyncio.to_thread(
                self._resolve_posix_focus_meta, win,
            )
            await self._emit_frame(
                ts_ns=time.time_ns(),
                handle=win.handle or 0,
                window_title=win.title,
                pid=pid,
                process_name=process_name,
            )
        except Exception:  # noqa: BLE001
            logger.debug("POSIX frame emit failed", exc_info=True)
            return True
        # Commit the change-detection markers only AFTER the frame published.
        # Claiming the window as "already reported" before the emit turned any
        # transient failure in the tail (a raising privacy rule, a bus handler,
        # a deep probe) into a permanent one: the next tick saw an unchanged
        # focus and stayed quiet, so ``state.current_frame`` kept describing the
        # PREVIOUS window until the user happened to switch again. Retrying one
        # interval later costs nothing and is the same reasoning behind
        # ``_reset_frame_state``.
        self._poll_last_handle = win.handle
        self._poll_last_title = win.title
        return True

    @staticmethod
    def _posix_foreground_window() -> WindowInfo | None:
        """Thin seam over ``window_state.foreground_window`` — tests patch
        this directly to simulate macOS/Linux without a real display."""
        return window_state.foreground_window()

    @staticmethod
    def _resolve_posix_focus_meta(win: WindowInfo) -> tuple[int, str]:
        """Best-effort ``(pid, process_name)`` for a foreground ``WindowInfo``.

        The pid the PROBE already resolved wins. ``window_state.foreground_window``
        fills ``WindowInfo.pid`` on all three platforms from the very same
        window it took the title from, so using it keeps title and process
        identity from the same instant. Re-querying "who is frontmost NOW"
        instead — as this did unconditionally — pairs app A's title with app
        B's process whenever focus moves between the probe and this call
        (a 2 s poll makes that window wide open), and the PrivacyFilter then
        judges a title against the wrong process name in both directions:
        a blocked app waved through, or an allowed one blocked.

        Only when the probe carries no pid does this fall back to a live
        query — macOS: ``NSWorkspace``'s frontmost application (needs neither
        the Screen-Recording nor the Accessibility grant); Linux:
        :func:`_linux_window_pid` resolves the owning pid from the X11 window id.
        ``psutil`` resolves the process name from the pid on both. Never raises —
        a missing pyobjc/xdotool or a transient lookup error degrades to
        ``(0, "")``; the frame still publishes and title-based privacy filtering
        still applies.

        A non-positive result is normalised to ``0`` rather than passed on. A
        negative pid would travel into ``FrameSnapshot.active_pid`` and every
        consumer's ``pid > 0`` guard as a value that looks resolved but names no
        process.
        """
        pid = int(win.pid or 0)
        if pid <= 0:
            plat = detect_platform()
            if plat == "darwin":
                try:
                    from AppKit import NSWorkspace  # type: ignore[import-not-found] # noqa: PLC0415

                    app = NSWorkspace.sharedWorkspace().frontmostApplication()
                    if app is not None:
                        pid = int(app.processIdentifier())
                except Exception:  # noqa: BLE001 — no pyobjc / transient AppKit error
                    logger.debug("macOS frontmost pid lookup failed", exc_info=True)
                    pid = 0
            elif plat == "linux" and win.handle:
                pid = _linux_window_pid(int(win.handle))
        if pid <= 0:
            return 0, ""

        process_name = ""
        try:
            import psutil  # noqa: PLC0415

            process_name = psutil.Process(pid).name()
        except Exception:  # noqa: BLE001
            process_name = ""
        return pid, process_name
