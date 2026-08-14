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

Two ways to die, not one
------------------------
A loop can fail to serve in two entirely different ways, and only the first one
is silence:

* **Stall** — the loop executes nothing at all, wedged inside one synchronous
  call. The liveness beat below never comes back. This is the 2026-07-28 shape.
* **Livelock** — the loop executes callbacks as fast as it can and still makes
  no progress, because one task spins on ``await asyncio.sleep(0)``. The beat
  comes back on time, every time, and a liveness-only watchdog reports perfect
  health while the product is frozen in front of the user.

The second shape cost a full evening on 2026-08-14: fifteen freezes in under
two and a half hours, each ended by killing the app. The GUI said "Python is
not responding", the web UI said "Offline", and the log simply stopped
mid-sentence — yet this watchdog, armed the whole time, never said a word,
because the loop it watched was demonstrably alive. The spin sat in anyio's TLS
write path underneath the Telegram long-poll: a ``send()`` that moved no bytes,
a ``sleep(0)``, and round again. One thread burning one core holds the GIL
against every other thread in the process, which is why the window froze and
why the log went quiet — the log writer is a thread too, and it never got
scheduled to drain its queue.

So liveness alone is not health. The watchdog also measures what the loop
thread COSTS: a loop thread that consumes a whole core for tens of seconds is
not working, it is spinning, and it gets the same treatment as a stall — the
stack, in the log, naming the call.
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

#: Share of one CPU core the loop thread must consume to count as spinning.
#: Real work on the loop is bursty and interleaved with waiting; a sustained
#: reading this close to 1.0 means the thread is never waiting at all, which no
#: healthy async workload does.
DEFAULT_SPIN_CPU_RATIO = 0.85

#: How long that saturation must hold before it is reported. Long enough that a
#: legitimate CPU-bound moment on the loop — a large JSON serialisation, a
#: model warm-up call that slipped onto the loop — passes without a word.
DEFAULT_SPIN_S = 20.0


class EventLoopWatchdog:
    """Watch one asyncio loop from a thread that the loop cannot block.

    Liveness is proved by the cheapest thing that still proves it: schedule a
    callback, see whether it ran. A loop executing anything at all drains its
    ready queue and the beat comes back within milliseconds; a loop stuck
    inside one synchronous call never reaches it, no matter how healthy the
    process looks from the outside.

    Progress is a second, independent question, and the reason this class does
    not stop at liveness: a spinning loop answers every beat and serves nobody.
    That is measured by the CPU time of the loop thread itself, so a busy
    ``sleep(0)`` cycle is told apart from an idle loop that simply has little
    to do.

    Args:
        loop: The loop to watch.
        interval_s: Seconds between liveness probes.
        stall_s: Silence beyond this is reported.
        repeat_s: How often an ongoing stall or spin is re-reported.
        spin_cpu_ratio: Share of one core (0..1) that counts as saturated.
        spin_s: How long saturation must hold before it is reported.
        on_stall: Receives ``(stalled_seconds, stack_text)``. Defaults to a
            loguru warning. Injected so the behaviour is testable without
            asserting on log output.
        on_livelock: Receives ``(spinning_seconds, cpu_ratio, stack_text)``.
            Defaults to a loguru warning, same reasoning as ``on_stall``.
        cpu_sampler: Returns the loop thread's CPU seconds so far, or ``None``
            when the platform cannot say. Defaults to the psutil reading below.
            Injected for the same reason as the reporters: so the spin logic can
            be tested against an exact CPU curve instead of a real busy loop.
    """

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        *,
        interval_s: float = DEFAULT_INTERVAL_S,
        stall_s: float = DEFAULT_STALL_S,
        repeat_s: float = DEFAULT_REPEAT_S,
        spin_cpu_ratio: float = DEFAULT_SPIN_CPU_RATIO,
        spin_s: float = DEFAULT_SPIN_S,
        on_stall: Callable[[float, str], None] | None = None,
        on_livelock: Callable[[float, float, str], None] | None = None,
        cpu_sampler: Callable[[], float | None] | None = None,
    ) -> None:
        self._loop = loop
        self._interval_s = max(0.1, float(interval_s))
        self._stall_s = max(self._interval_s, float(stall_s))
        self._repeat_s = max(self._interval_s, float(repeat_s))
        self._spin_cpu_ratio = float(spin_cpu_ratio)
        self._spin_s = max(self._interval_s, float(spin_s))
        self._on_stall = on_stall or _log_stall
        self._on_livelock = on_livelock or _log_livelock
        self._cpu_sampler = cpu_sampler or self._loop_thread_cpu_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # Guards the two timestamps below, which the loop thread writes and the
        # watchdog thread reads.
        self._lock = threading.Lock()
        self._last_beat = time.monotonic()
        #: Thread id of the loop, learned from the loop itself rather than
        #: assumed — the backend loop does not run on the thread that built it.
        self._loop_thread_id: int | None = None
        #: The OS-level id of the same thread. ``get_ident`` is a Python handle
        #: and does not address the thread in psutil's per-thread table.
        self._loop_native_id: int | None = None
        self._reported_at: float | None = None
        # Spin bookkeeping, owned by the watchdog thread alone.
        self._last_cpu_s: float | None = None
        self._last_cpu_at: float | None = None
        self._spinning_since: float | None = None
        self._spin_reported_at: float | None = None

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
        self._loop_native_id = threading.get_native_id()

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

            # A stalled loop cannot also be spinning: the beat that feeds the
            # spin measurement is exactly what it is failing to deliver.
            if silent_for >= self._stall_s:
                self._reset_spin()
                now = time.monotonic()
                if reported_at is not None and now - reported_at < self._repeat_s:
                    continue
                with self._lock:
                    self._reported_at = now
                try:
                    self._on_stall(silent_for, self._loop_stack())
                except Exception:  # noqa: BLE001, S110 - reporting never kills the watchdog
                    pass
                continue

            self._check_spin()

    # ---- Progress (as opposed to mere liveness) -----------------------
    def _reset_spin(self) -> None:
        """Forget the current saturation streak and its sampling baseline."""
        self._spinning_since = None
        self._spin_reported_at = None
        self._last_cpu_s = None
        self._last_cpu_at = None

    def _check_spin(self) -> None:
        """Report a loop that answers every beat and still serves nobody."""
        cpu_s = self._cpu_sampler()
        now = time.monotonic()
        if cpu_s is None:
            # No per-thread accounting on this platform or build. The stall
            # half of the watchdog stands on its own; this half stays quiet
            # rather than guessing from process-wide CPU, which any busy
            # worker thread would trip.
            self._reset_spin()
            return

        previous_cpu = self._last_cpu_s
        previous_at = self._last_cpu_at
        self._last_cpu_s = cpu_s
        self._last_cpu_at = now
        if previous_cpu is None or previous_at is None:
            return

        elapsed = now - previous_at
        if elapsed <= 0:
            return
        ratio = (cpu_s - previous_cpu) / elapsed

        if ratio < self._spin_cpu_ratio:
            self._spinning_since = None
            self._spin_reported_at = None
            return

        if self._spinning_since is None:
            self._spinning_since = previous_at
        spinning_for = now - self._spinning_since
        if spinning_for < self._spin_s:
            return
        if (
            self._spin_reported_at is not None
            and now - self._spin_reported_at < self._repeat_s
        ):
            return
        self._spin_reported_at = now
        try:
            self._on_livelock(spinning_for, ratio, self._loop_stack())
        except Exception:  # noqa: BLE001, S110 - reporting never kills the watchdog
            pass

    def _loop_thread_cpu_s(self) -> float | None:
        """CPU seconds burned by the loop thread, or ``None`` if unknowable.

        Per-thread accounting is what separates "the loop is spinning" from
        "some other thread in this process is busy" — a distinction the
        process-wide clock cannot make in an app that also runs speech, audio
        and terminal threads. psutil is an ordinary runtime dependency here,
        but the reading stays optional on purpose: an OS or build that refuses
        the per-thread table degrades to stall detection only, quietly.
        """
        native_id = self._loop_native_id
        if native_id is None:
            return None
        try:
            import psutil  # type: ignore[import-untyped]
        except ImportError:
            return None
        try:
            for thread in psutil.Process().threads():
                if thread.id == native_id:
                    return float(thread.user_time + thread.system_time)
        except Exception:  # noqa: BLE001 - a diagnostic never raises at the caller
            return None
        return None

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


def _log_livelock(spinning_s: float, cpu_ratio: float, stack_text: str) -> None:
    """Default reporter for a loop that is busy without getting anywhere."""
    from loguru import logger

    logger.warning(
        "Event loop SPINNING for {:.1f}s at {:.0f}% of one core — it answers "
        "every liveness probe and serves nobody. One thread at this cost holds "
        "the GIL against the whole process: the window stops repainting, the "
        "log writer stops draining. Stack of the loop thread:\n{}",
        spinning_s,
        cpu_ratio * 100,
        stack_text,
    )


__all__ = [
    "DEFAULT_INTERVAL_S",
    "DEFAULT_REPEAT_S",
    "DEFAULT_SPIN_CPU_RATIO",
    "DEFAULT_SPIN_S",
    "DEFAULT_STALL_S",
    "EventLoopWatchdog",
]
