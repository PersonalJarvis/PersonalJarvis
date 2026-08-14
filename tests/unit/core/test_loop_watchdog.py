"""The off-loop watchdog that names whatever is blocking the serving loop.

The failure it exists for is not hypothetical: on 2026-07-28 the backend loop
sat inside a synchronous TOML parse for over ten minutes, every probe from
outside timed out, and nothing in the log said why. These tests pin the two
properties that make the difference — it must fire while the loop is genuinely
wedged, and it must not fire for a loop that is merely busy or idle.

A loop can also fail the opposite way, and on 2026-08-14 it did: fifteen
freezes in an evening where the loop answered every liveness beat on time and
still served nobody, because one task spun on ``await asyncio.sleep(0)`` at a
full core. A liveness-only watchdog calls that perfect health. The second half
of these tests pins the reading that tells the two apart — what the loop thread
COSTS — including that it stays quiet for an ordinary CPU burst and that it
gives up quietly where per-thread CPU cannot be read at all.
"""
from __future__ import annotations

import asyncio
import threading
import time

import pytest

from jarvis.core.loop_watchdog import EventLoopWatchdog


class _Recorder:
    """Collects stall reports without going anywhere near the logger."""

    def __init__(self) -> None:
        self.calls: list[tuple[float, str]] = []
        self.seen = threading.Event()

    def __call__(self, stalled_s: float, stack: str) -> None:
        self.calls.append((stalled_s, stack))
        self.seen.set()


def _run_loop_in_thread() -> tuple[asyncio.AbstractEventLoop, threading.Thread]:
    """A real loop on its own thread — the shape the backend actually runs."""
    loop = asyncio.new_event_loop()
    ready = threading.Event()

    def _serve() -> None:
        asyncio.set_event_loop(loop)
        loop.call_soon(ready.set)
        loop.run_forever()

    thread = threading.Thread(target=_serve, name="test-loop", daemon=True)
    thread.start()
    assert ready.wait(5.0), "the test loop never started"
    return loop, thread


@pytest.fixture
def live_loop():
    loop, thread = _run_loop_in_thread()
    yield loop
    loop.call_soon_threadsafe(loop.stop)
    thread.join(timeout=5.0)
    loop.close()


def test_a_healthy_loop_is_never_reported(live_loop):
    """An idle loop answers every beat, so nothing is logged."""
    recorder = _Recorder()
    watchdog = EventLoopWatchdog(
        live_loop, interval_s=0.05, stall_s=0.5, on_stall=recorder
    )
    watchdog.start()
    try:
        time.sleep(1.2)  # comfortably longer than the stall threshold
    finally:
        watchdog.stop()
    assert recorder.calls == [], "a responsive loop must stay silent"


def test_a_blocked_loop_is_reported(live_loop):
    """A synchronous call on the loop is exactly what must be caught."""
    recorder = _Recorder()
    watchdog = EventLoopWatchdog(
        live_loop, interval_s=0.05, stall_s=0.4, on_stall=recorder
    )
    watchdog.start()
    release = threading.Event()

    def _wedge() -> None:
        # Stands in for the real thing: one synchronous call the loop cannot
        # yield out of. time.sleep holds the loop exactly as a slow parse does.
        release.wait(3.0)

    try:
        live_loop.call_soon_threadsafe(_wedge)
        assert recorder.seen.wait(4.0), "a wedged loop went unreported"
    finally:
        release.set()
        watchdog.stop()

    stalled_s, stack = recorder.calls[0]
    assert stalled_s >= 0.4
    assert "_wedge" in stack, f"the report must name the blocking call, got:\n{stack}"


def test_an_ongoing_stall_is_not_repeated_every_check(live_loop):
    """A wedged loop leaves a readable trail, not one block per interval."""
    recorder = _Recorder()
    watchdog = EventLoopWatchdog(
        live_loop,
        interval_s=0.05,
        stall_s=0.2,
        repeat_s=30.0,  # far beyond the test's lifetime
        on_stall=recorder,
    )
    watchdog.start()
    release = threading.Event()

    try:
        live_loop.call_soon_threadsafe(lambda: release.wait(2.0))
        assert recorder.seen.wait(3.0)
        time.sleep(0.6)  # many further checks, all inside the same stall
    finally:
        release.set()
        watchdog.stop()

    assert len(recorder.calls) == 1, (
        f"an ongoing stall must be reported once per repeat window, "
        f"got {len(recorder.calls)}"
    )


def test_recovery_re_arms_the_report(live_loop):
    """After the loop frees itself, a LATER stall is reported again."""
    recorder = _Recorder()
    watchdog = EventLoopWatchdog(
        live_loop, interval_s=0.05, stall_s=0.2, repeat_s=30.0, on_stall=recorder
    )
    watchdog.start()

    try:
        first = threading.Event()
        live_loop.call_soon_threadsafe(lambda: first.wait(1.0))
        assert recorder.seen.wait(3.0)
        first.set()

        time.sleep(0.3)  # let the loop beat again — this clears the report
        recorder.seen.clear()

        second = threading.Event()
        live_loop.call_soon_threadsafe(lambda: second.wait(1.0))
        assert recorder.seen.wait(3.0), "a fresh stall after recovery went unreported"
        second.set()
    finally:
        watchdog.stop()

    assert len(recorder.calls) == 2


def test_stop_is_idempotent_and_start_does_not_double_up(live_loop):
    """Lifecycle calls are safe to repeat — boot paths retry things."""
    watchdog = EventLoopWatchdog(live_loop, interval_s=0.05, stall_s=5.0)
    watchdog.start()
    watchdog.start()  # must not spawn a second thread
    watchdog.stop()
    watchdog.stop()  # must not raise


def test_a_closed_loop_retires_the_watchdog_quietly():
    """Shutdown is not a stall — the watchdog goes down with the loop."""
    loop, thread = _run_loop_in_thread()
    recorder = _Recorder()
    watchdog = EventLoopWatchdog(
        loop, interval_s=0.05, stall_s=0.2, on_stall=recorder
    )
    watchdog.start()

    loop.call_soon_threadsafe(loop.stop)
    thread.join(timeout=5.0)
    loop.close()

    time.sleep(0.5)
    watchdog.stop()
    assert recorder.calls == [], "a closed loop must not be reported as stalled"


def test_stack_is_reported_even_before_any_beat_landed(live_loop):
    """The reporter never raises, even asked before the loop identified itself."""
    watchdog = EventLoopWatchdog(live_loop, interval_s=5.0, stall_s=5.0)
    text = watchdog._loop_stack()
    assert isinstance(text, str) and text


# ----------------------------------------------------------------------
# Livelock: the loop answers every beat and still serves nobody
# ----------------------------------------------------------------------


class _SpinRecorder:
    """Collects livelock reports without going anywhere near the logger."""

    def __init__(self) -> None:
        self.calls: list[tuple[float, float, str]] = []
        self.seen = threading.Event()

    def __call__(self, spinning_s: float, cpu_ratio: float, stack: str) -> None:
        self.calls.append((spinning_s, cpu_ratio, stack))
        self.seen.set()


class _FakeCpu:
    """A CPU clock that burns a fixed share of wall time.

    Standing in for the real per-thread reading keeps these tests exact and
    quick: a genuine busy loop would have to run for the whole spin window at
    whatever share of a core the CI box happens to give it.
    """

    def __init__(self, ratio: float) -> None:
        self._ratio = ratio
        self._base = time.monotonic()

    def __call__(self) -> float:
        return (time.monotonic() - self._base) * self._ratio


def test_a_spinning_loop_is_reported(live_loop):
    """A loop at a full core is not healthy, however promptly it beats."""
    stalls, spins = _Recorder(), _SpinRecorder()
    watchdog = EventLoopWatchdog(
        live_loop,
        interval_s=0.05,
        stall_s=5.0,  # far away: this loop is responsive, not wedged
        spin_s=0.3,
        on_stall=stalls,
        on_livelock=spins,
        cpu_sampler=_FakeCpu(1.0),
    )
    watchdog.start()
    try:
        assert spins.seen.wait(4.0), "a loop burning a full core went unreported"
    finally:
        watchdog.stop()

    spinning_s, ratio, stack = spins.calls[0]
    assert spinning_s >= 0.3
    assert ratio >= 0.85
    assert isinstance(stack, str) and stack
    assert stalls.calls == [], "a spinning loop is not a stalled loop"


def test_a_waiting_loop_is_not_reported_as_spinning(live_loop):
    """The ordinary case — a loop that mostly waits — must stay silent."""
    spins = _SpinRecorder()
    watchdog = EventLoopWatchdog(
        live_loop,
        interval_s=0.05,
        stall_s=5.0,
        spin_s=0.2,
        on_livelock=spins,
        cpu_sampler=_FakeCpu(0.05),
    )
    watchdog.start()
    try:
        time.sleep(1.0)  # many checks, all well below the threshold
    finally:
        watchdog.stop()
    assert spins.calls == [], "a loop that waits must never be called a livelock"


def test_a_brief_cpu_burst_is_not_reported(live_loop):
    """Serialising a big response is allowed to cost a moment of core."""
    spins = _SpinRecorder()
    # Saturated the whole time, but the window is longer than the test lives:
    # the streak never matures into a report.
    watchdog = EventLoopWatchdog(
        live_loop,
        interval_s=0.05,
        stall_s=5.0,
        spin_s=30.0,
        on_livelock=spins,
        cpu_sampler=_FakeCpu(1.0),
    )
    watchdog.start()
    try:
        time.sleep(0.8)
    finally:
        watchdog.stop()
    assert spins.calls == [], "a short burst of CPU is work, not a livelock"


def test_an_ongoing_spin_is_not_repeated_every_check(live_loop):
    """A spin wedged for minutes leaves a trail, not one block per interval."""
    spins = _SpinRecorder()
    watchdog = EventLoopWatchdog(
        live_loop,
        interval_s=0.05,
        stall_s=5.0,
        spin_s=0.2,
        repeat_s=30.0,  # far beyond the test's lifetime
        on_livelock=spins,
        cpu_sampler=_FakeCpu(1.0),
    )
    watchdog.start()
    try:
        assert spins.seen.wait(3.0)
        time.sleep(0.5)  # many further checks, all inside the same spin
    finally:
        watchdog.stop()
    assert len(spins.calls) == 1, (
        f"an ongoing spin must be reported once per repeat window, "
        f"got {len(spins.calls)}"
    )


def test_unreadable_thread_cpu_degrades_to_stall_detection(live_loop):
    """Where per-thread CPU is unknowable, the watchdog keeps its other half.

    Guessing from process-wide CPU would blame the loop for whatever a speech
    or terminal thread happens to be doing, so the honest move is silence on
    spin and business as usual on stall.
    """
    stalls, spins = _Recorder(), _SpinRecorder()
    watchdog = EventLoopWatchdog(
        live_loop,
        interval_s=0.05,
        stall_s=0.4,
        spin_s=0.2,
        on_stall=stalls,
        on_livelock=spins,
        cpu_sampler=lambda: None,
    )
    watchdog.start()
    release = threading.Event()
    try:
        time.sleep(0.5)
        assert spins.calls == [], "no CPU reading must mean no spin verdict"
        live_loop.call_soon_threadsafe(lambda: release.wait(3.0))
        assert stalls.seen.wait(4.0), "stall detection must survive on its own"
    finally:
        release.set()
        watchdog.stop()


def test_the_real_cpu_sampler_answers_for_a_live_loop(live_loop):
    """The default reading works on this platform, or says so by returning None."""
    watchdog = EventLoopWatchdog(live_loop, interval_s=0.05, stall_s=5.0)
    watchdog.start()
    try:
        time.sleep(0.3)  # let a beat land so the loop thread identifies itself
        reading = watchdog._loop_thread_cpu_s()
    finally:
        watchdog.stop()
    assert reading is None or (isinstance(reading, float) and reading >= 0.0)
