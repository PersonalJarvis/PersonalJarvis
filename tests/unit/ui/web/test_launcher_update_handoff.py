"""A freshly installed native build takes over from the OLDER build it replaces.

Native builds up to v2.4.x start the new version while they are still running
(macOS ``open -n`` and the Linux AppImage respawn fire right after the swap).
The new version used to find the lock held, focus the old window and exit —
then the old app quit too, and nothing was left running. These tests pin the
hand-off: an older holder is waited out, everything else falls through to the
normal "already running" handling untouched.
"""

from __future__ import annotations

from jarvis.ui import desktop_app
from jarvis.ui.web import launcher


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


def _acquire_after(attempts: int):
    calls = {"n": 0}

    def acquire():
        calls["n"] += 1
        if calls["n"] <= attempts:
            raise desktop_app.SingleInstanceError("held")
        return "LOCK"

    return acquire, calls


def _wait(**overrides):
    clock = _Clock()
    kwargs = {
        "frozen": True,
        "read_meta": lambda: {"port": 47821, "pid": 1},
        "version_of": lambda _port: "2.4.4",
        "running_version": "2.5.0",
        "sleep": clock.sleep,
        "now": clock.now,
        "wait_s": 90.0,
    }
    kwargs.update(overrides)
    return launcher._wait_out_an_older_holder(**kwargs), clock


def test_an_older_holder_is_waited_out_and_the_lock_taken() -> None:
    acquire, calls = _acquire_after(3)
    lock, clock = _wait(acquire=acquire)
    assert lock == "LOCK"
    assert calls["n"] == 4
    assert clock.t == 1.5


def test_the_same_version_is_a_normal_second_launch() -> None:
    acquire, calls = _acquire_after(0)
    lock, _ = _wait(acquire=acquire, version_of=lambda _port: "2.5.0")
    assert lock is None
    assert calls["n"] == 0


def test_a_newer_holder_is_never_replaced() -> None:
    acquire, calls = _acquire_after(0)
    lock, _ = _wait(acquire=acquire, version_of=lambda _port: "2.6.0")
    assert lock is None
    assert calls["n"] == 0


def test_a_source_install_never_waits() -> None:
    acquire, calls = _acquire_after(0)
    lock, _ = _wait(acquire=acquire, frozen=False)
    assert lock is None
    assert calls["n"] == 0


def test_a_silent_holder_falls_through_to_recovery() -> None:
    acquire, calls = _acquire_after(0)
    lock, _ = _wait(acquire=acquire, version_of=lambda _port: None)
    assert lock is None
    assert calls["n"] == 0


def test_no_sidecar_means_no_hand_off() -> None:
    acquire, calls = _acquire_after(0)
    lock, _ = _wait(acquire=acquire, read_meta=lambda: None)
    assert lock is None
    assert calls["n"] == 0


def test_an_older_holder_that_never_quits_gives_up_at_the_deadline() -> None:
    acquire, _ = _acquire_after(10_000)
    lock, clock = _wait(acquire=acquire, wait_s=5.0)
    assert lock is None
    assert clock.t >= 5.0


def test_garbage_versions_are_not_older() -> None:
    assert launcher._is_older("not-a-version", "2.5.0") is False
    assert launcher._is_older("2.4.4", "2.5.0") is True
    assert launcher._is_older("2.5.0", "2.5.0") is False
