"""The JarvisBar's Tk thread must be woken from OUTSIDE Tk when it falls asleep.

Forensic (BUG-202, 2026-08-28): "Hey Jarvis" was answered by voice while the
bar stayed on its collapsed idle pill. py-spy showed the Tk thread parked in
``GetMessage`` with every ``after`` chain armed and none firing — Tcl's
Windows notifier had lost its timer wake-up once, and a settled idle bar
generates no other message that returns from that wait. The in-Tk watchdog
shares that timer queue, so it slept too. One posted ``WM_NULL`` revived the
loop; these tests pin the waker that posts it. Headless — no real Tk display.
"""

from __future__ import annotations

import sys
import time

from jarvis.core import process_utils
from jarvis.ui.jarvisbar.overlay import (
    LOOP_ASLEEP_THRESHOLD_NS,
    SHOW_WAKE_THRESHOLD_NS,
    JarvisBarOverlay,
)


class _FakeRoot:
    pass


def _bare_bar(*, heartbeat_age_ns: int | None) -> tuple[JarvisBarOverlay, list[int]]:
    """A bar built without __init__ (the file's own test convention) whose
    wake-up call is recorded instead of hitting the OS."""
    bar = JarvisBarOverlay.__new__(JarvisBarOverlay)
    bar._running = True
    bar._root = _FakeRoot()
    bar._mode = "idle"
    bar._persistent = True
    bar._startup_gated = False
    bar._tk_native_thread_id = 4242
    bar._last_wake_ns = 0
    bar._sleep_kicks = 0
    now = time.monotonic_ns()
    bar._last_frame_ns = 0 if heartbeat_age_ns is None else now - heartbeat_age_ns
    kicks: list[int] = []
    bar._wake_tk_loop = lambda: kicks.append(1) or True  # type: ignore[method-assign]
    bar._enqueue_ui = lambda fn: None  # type: ignore[method-assign]
    return bar, kicks


def test_waker_kicks_a_sleeping_loop():
    bar, kicks = _bare_bar(heartbeat_age_ns=LOOP_ASLEEP_THRESHOLD_NS * 2)

    assert bar._kick_if_asleep() is True
    assert kicks == [1], "a stale heartbeat did not post a wake-up"
    assert bar._sleep_kicks == 1


def test_waker_leaves_a_ticking_loop_alone():
    bar, kicks = _bare_bar(heartbeat_age_ns=0)

    assert bar._kick_if_asleep() is False
    assert kicks == [], "a fresh heartbeat must never be kicked"


def test_waker_holds_off_before_the_first_frame():
    """0 = no frame has run yet: the loop is booting, not asleep."""
    bar, kicks = _bare_bar(heartbeat_age_ns=None)

    assert bar._kick_if_asleep() is False
    assert kicks == []


def test_waker_posts_at_most_one_wake_per_window():
    """A thread that is busy rather than waiting gets one nudge per threshold
    window, not one per poll — the message queue must not fill with no-ops."""
    bar, kicks = _bare_bar(heartbeat_age_ns=LOOP_ASLEEP_THRESHOLD_NS * 2)
    now = time.monotonic_ns()

    assert bar._kick_if_asleep(now_ns=now) is True
    assert bar._kick_if_asleep(now_ns=now + LOOP_ASLEEP_THRESHOLD_NS // 2) is False
    assert bar._kick_if_asleep(now_ns=now + LOOP_ASLEEP_THRESHOLD_NS + 1) is True
    assert kicks == [1, 1]


def test_waker_closes_the_sleep_once_the_heartbeat_returns():
    bar, kicks = _bare_bar(heartbeat_age_ns=LOOP_ASLEEP_THRESHOLD_NS * 2)
    bar._kick_if_asleep()
    assert bar._sleep_kicks == 1

    bar._last_frame_ns = time.monotonic_ns()  # the loop ticked again
    assert bar._kick_if_asleep() is False
    assert bar._sleep_kicks == 0, "a recovered loop must reset the sleep counter"
    assert kicks == [1]


def test_waker_does_nothing_once_stopped():
    bar, kicks = _bare_bar(heartbeat_age_ns=LOOP_ASLEEP_THRESHOLD_NS * 2)
    bar._running = False

    assert bar._kick_if_asleep() is False
    assert kicks == []


def test_show_wakes_a_sleeping_loop_at_once():
    """The reveal is the moment the user looks: ``show()`` must not wait for the
    waker's next poll. Its own threshold is far below the poll threshold."""
    assert SHOW_WAKE_THRESHOLD_NS < LOOP_ASLEEP_THRESHOLD_NS
    bar, kicks = _bare_bar(heartbeat_age_ns=SHOW_WAKE_THRESHOLD_NS * 2)

    bar.show("listen")

    assert bar._mode == "listen"
    assert kicks == [1], "show() on a quiet loop did not post a wake-up"


def test_show_on_a_ticking_loop_posts_nothing():
    bar, kicks = _bare_bar(heartbeat_age_ns=0)

    bar.show("listen")

    assert bar._mode == "listen"
    assert kicks == []


def test_wake_helper_is_a_quiet_no_op_off_windows(monkeypatch):
    """Cross-platform rule: OS-specific code sits behind a capability check
    and degrades to a quiet no-op elsewhere — no thread, no exception."""
    monkeypatch.setattr(sys, "platform", "linux")

    assert process_utils.thread_message_loop_wake_supported() is False
    assert process_utils.wake_thread_message_loop(4242) is False


def test_waker_thread_is_not_started_without_a_message_queue(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    bar, _kicks = _bare_bar(heartbeat_age_ns=0)
    started: list[str] = []

    import threading

    class _NoThread:
        def __init__(self, *a: object, **k: object) -> None:
            started.append(str(k.get("name")))

        def start(self) -> None:  # pragma: no cover - must never run
            raise AssertionError("waker thread started on a platform without a queue")

    monkeypatch.setattr(threading, "Thread", _NoThread)
    bar._start_loop_waker()

    assert started == []


def test_wake_helper_rejects_a_dead_thread_on_windows():
    """On Windows a thread id nobody owns has no queue: the call reports False
    and raises nothing."""
    if sys.platform != "win32":
        return
    assert process_utils.thread_message_loop_wake_supported() is True
    assert process_utils.wake_thread_message_loop(0x7FFFFFFF) is False
