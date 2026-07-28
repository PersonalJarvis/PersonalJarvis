"""Notice when the serving event loop stops serving — and say what is holding it.

Every WebSocket, every HTTP route and every brain turn shares one asyncio loop.
A single synchronous call on it stops all of them at once, and the symptom the
user reports is never "the loop is blocked": it is that the window says "Not
responding", that clicks land late, and that characters typed into an
Agentic-IDE pane appear seconds after they were typed — because a keystroke is
a WebSocket frame waiting behind whatever is running.

The diagnostics route ``/api/diagnostics/event-loop-lag`` already measures this,
but it cannot report the case that matters: it runs ON the loop, so a loop that
has stopped answers nothing at all. On 2026-07-28 the backend sat inside
``tomllib`` for over ten minutes and every probe from the outside — health
included — timed out with no explanation anywhere. Finding out what it was
required attaching a sampling profiler to a live process.

So this watchdog lives OFF the loop, in a plain daemon thread, and asks the one
question that survives a stall: did the loop run anything since I last looked?
When it did not, the offending Python stack goes into the log, which is where
the next person to hit this will actually look.
"""
from __future__ import annotations

import sys
import threading
import time
import traceback
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    import asyncio

#: How often the watchdog asks the loop to prove it is alive.
DEFAULT_INTERVAL_S = 5.0

#: Silence beyond this is reported. Well above an ordinary slow moment — a
#: garbage collection pause, a big JSON response, a burst of terminal output —
#: because a watchdog that cries wolf gets muted, and the failure it exists for
#: lasts minutes, not milliseconds.
DEFAULT_STALL_S = 15.0

#: A stall that persists is logged again at this interval rather than once per
#: check, so a loop wedged for ten minutes leaves a readable trail instead of
#: 120 identical blocks.
DEFAULT_REPEAT_S = 60.0


class EventLoopWatchdog:
    """Watch one asyncio loop from a thread that the loop cannot block.

    The mechanism is deliberately the cheapest thing that still proves
    liveness: schedule a callback, see whether it ran. A loop executing
    anything at all drains its ready queue and the beat comes back within
    milliseconds; a loop stuck inside one synchronous call never reaches it, no
    matter how healthy the process looks from the outside.

    Args:
        loop: The loop to watch.
        interval_s: Seconds between liveness probes.
        stall_s: Silence beyond this is reported.
        repeat_s: How often an ongoing stall is re-reported.
        on_stall: Receives ``(stalled_seconds, stack_text)``. Defaults to a
            loguru warning. Injected so the behaviour is testable without
            asserting on log output.
    """

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        *,
        interval_s: float = DEFAULT_INTERVAL_S,
        stall_s: float = DEFAULT_STALL_S,
        repeat_s: float = DEFAULT_REPEAT_S,
        on_stall: Callable[[float, str], None] | None = None,
    ) -> None:
        self._loop = loop
        self._interval_s = max(0.1, float(interval_s))
        self._stall_s = max(self._interval_s, float(stall_s))
        self._repeat_s = max(self._interval_s, float(repeat_s))
        self._on_stall = on_stall or _log_stall
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # Guards the two timestamps below, which the loop thread writes and the
        # watchdog thread reads.
        self._lock = threading.Lock()
        self._last_beat = time.monotonic()
        #: Thread id of the loop, learned from the loop itself rather than
        #: assumed — the backend loop does not run on the thread that built it.
        self._loop_thread_id: int | None = None
        self._reported_at: float | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def start(self) -> None:
        """Begin watching. Idempotent; safe to call on a loop already running."""
        if self._thread is not None:
            return
        self._stop.clear()
        with self._lock:
            self._last_beat = time.monotonic()
        self._thread = threading.Thread(
            target=self._run,
            name="jarvis-loop-watchdog",
            daemon=True,
        )
        self._thread.start()

    def stop(self, *, timeout: float = 2.0) -> None:
        """Stop watching and wait briefly for the thread to unwind."""
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _beat(self) -> None:
        """Runs ON the loop — the proof that it is still executing callbacks."""
        with self._lock:
            self._last_beat = time.monotonic()
            self._reported_at = None
        # Learned here rather than at construction: whoever built the watchdog
        # is usually not the thread the loop ends up running on.
        self._loop_thread_id = threading.get_ident()

    def _run(self) -> None:
        while not self._stop.is_set():
            # A closing loop rejects the callback; that is a shutdown, not a
            # stall, and the watchdog simply retires with it.
            try:
                self._loop.call_soon_threadsafe(self._beat)
            except RuntimeError:
                return
            except Exception:  # noqa: BLE001 - a watchdog never takes the app down
                return

            if self._stop.wait(self._interval_s):
                return

            with self._lock:
                silent_for = time.monotonic() - self._last_beat
                reported_at = self._reported_at

            if silent_for < self._stall_s:
                continue
            now = time.monotonic()
            if reported_at is not None and now - reported_at < self._repeat_s:
                continue
            with self._lock:
                self._reported_at = now
            try:
                self._on_stall(silent_for, self._loop_stack())
            except Exception:  # noqa: BLE001, S110 - reporting never kills the watchdog
                pass

    def _loop_stack(self) -> str:
        """The Python stack of the loop thread, as of now.

        This is the whole value of the watchdog: not that something is slow,
        but which call it is sitting in. ``sys._current_frames`` reads it
        without a debugger, a profiler, or cooperation from the wedged thread.
        """
        thread_id = self._loop_thread_id
        if thread_id is None:
            return "<loop thread never identified — it has not run a callback yet>"
        frame = sys._current_frames().get(thread_id)
        if frame is None:
            return f"<no frame for loop thread {thread_id}>"
        return "".join(traceback.format_stack(frame))


def _log_stall(stalled_s: float, stack_text: str) -> None:
    """Default reporter: one warning naming the duration and the guilty stack."""
    from loguru import logger

    logger.warning(
        "Event loop STALLED for {:.1f}s — every WebSocket, HTTP route and "
        "brain turn is blocked behind this call. Stack of the loop thread:\n{}",
        stalled_s,
        stack_text,
    )


__all__ = [
    "DEFAULT_INTERVAL_S",
    "DEFAULT_REPEAT_S",
    "DEFAULT_STALL_S",
    "EventLoopWatchdog",
]
