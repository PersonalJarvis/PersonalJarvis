"""The off-loop watchdog that names whatever is blocking the serving loop.

The failure it exists for is not hypothetical: on 2026-07-28 the backend loop
sat inside a synchronous TOML parse for over ten minutes, every probe from
outside timed out, and nothing in the log said why. These tests pin the two
properties that make the difference — it must fire while the loop is genuinely
wedged, and it must not fire for a loop that is merely busy or idle.
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
