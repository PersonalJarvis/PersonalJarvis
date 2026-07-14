"""Permanent fix for the post-reboot audio-device-index drift (BUG-014 class).

Symptom (2026-05-25): the user rebooted, said "Hey Jarvis", the wake word was
detected (score 0.962) and the state machine woke — but no chime/answer was
audible. Root cause: Jarvis autostarts before Windows finishes enumerating
audio endpoints. PortAudio froze a partial device table (4 instead of 7
devices) for the whole process; the resolved speaker index then pointed at a
stale/silent endpoint (idx 14 = monitor instead of the USB headset) and every
output stream evaporated.

``wait_for_stable_audio_devices`` waits until the device enumeration stops
changing, forcing PortAudio to re-scan on each poll so a too-early boot can no
longer pin a partial table. It must be robust on a headless VPS (no
sounddevice, no audio hardware) and must never raise.
"""
from __future__ import annotations

import jarvis.audio.device_init as di
from jarvis.audio.device_init import wait_for_stable_audio_devices


class FakeClock:
    """Deterministic monotonic clock — ``sleep`` advances virtual time."""

    def __init__(self) -> None:
        self.t = 0.0

    def monotonic(self) -> float:
        return self.t

    def sleep(self, dt: float) -> None:
        self.t += dt


def _dev(n_in: int = 0, n_out: int = 0) -> dict:
    return {"max_input_channels": n_in, "max_output_channels": n_out}


class FakeSD:
    """Fake sounddevice. ``query_devices`` returns successive lists; the last
    list repeats once exhausted. Counts PortAudio re-init calls."""

    def __init__(self, device_lists: list[list[dict]]) -> None:
        self._lists = device_lists
        self._idx = 0
        self.terminate_calls = 0
        self.initialize_calls = 0

    def _terminate(self) -> None:
        self.terminate_calls += 1

    def _initialize(self) -> None:
        self.initialize_calls += 1

    def query_devices(self) -> list[dict]:
        i = min(self._idx, len(self._lists) - 1)
        self._idx += 1
        return self._lists[i]


def test_waits_until_device_count_stabilizes(monkeypatch) -> None:
    """The early-boot case: table grows 4 -> 7 then settles. The function must
    keep polling until the count is stable for the whole window and report 7."""
    fake = FakeSD([[_dev(1, 0)] * 4, [_dev(1, 0)] * 7])
    monkeypatch.setattr(di, "_get_sd", lambda: fake)
    clock = FakeClock()

    info = wait_for_stable_audio_devices(
        max_wait_s=10.0,
        stable_window_s=1.5,
        poll_interval_s=0.5,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    assert info["device_count"] == 7
    assert info["stable"] is True
    # PortAudio was forced to re-enumerate (otherwise query_devices would
    # return the same cached partial table forever).
    assert fake.terminate_calls >= 1
    assert fake.initialize_calls >= 1


def test_no_sounddevice_returns_immediately(monkeypatch) -> None:
    """Headless VPS: sounddevice not installed. No wait, no raise, graceful."""
    monkeypatch.setattr(di, "_get_sd", lambda: None)
    clock = FakeClock()

    info = wait_for_stable_audio_devices(
        max_wait_s=10.0,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    assert info["available"] is False
    assert info["device_count"] == 0
    assert clock.t == 0.0  # did not burn any wait time


def test_never_raises_when_query_devices_errors(monkeypatch) -> None:
    """PortAudio can throw mid-scan. The function must swallow it and return a
    dict rather than crash the warm-up / boot."""

    class BoomSD:
        def _terminate(self) -> None: ...
        def _initialize(self) -> None: ...
        def query_devices(self):
            raise RuntimeError("PortAudio boom")

    monkeypatch.setattr(di, "_get_sd", lambda: BoomSD())
    clock = FakeClock()

    info = wait_for_stable_audio_devices(
        max_wait_s=1.0,
        stable_window_s=0.5,
        poll_interval_s=0.25,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    assert isinstance(info, dict)
    assert info["device_count"] == 0


def test_respects_max_wait_when_never_stable(monkeypatch) -> None:
    """Pathological flapping: the count changes every poll. The function must
    give up at max_wait rather than block boot forever."""

    class GrowSD:
        def __init__(self) -> None:
            self.n = 0

        def _terminate(self) -> None: ...
        def _initialize(self) -> None: ...
        def query_devices(self) -> list[dict]:
            self.n += 1
            return [_dev(1, 0)] * self.n

    monkeypatch.setattr(di, "_get_sd", lambda: GrowSD())
    clock = FakeClock()

    info = wait_for_stable_audio_devices(
        max_wait_s=2.0,
        stable_window_s=1.5,
        poll_interval_s=0.5,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    assert info["stable"] is False
    assert clock.t <= 2.5  # bounded by max_wait_s, never unbounded


# --- Boot-path prefetch (2026-06-24 boot-speed work) ----------------------
# The launcher starts the (blocking ~1.5 s) device settle in a daemon thread at
# process start so Phase-A warm-up reuses the settled result instead of
# re-paying the poll wait. Monotonically safe: get_prefetched_audio_result()
# returns None while still polling, so the caller falls back to a fresh settle.


def _reset_prefetch() -> None:
    di._PREFETCH_EVENT.clear()
    di._PREFETCH_RESULT = None
    di._PREFETCH_STARTED = False


def test_prefetch_result_none_before_any_prefetch() -> None:
    _reset_prefetch()
    assert di.get_prefetched_audio_result() is None


def test_prefetch_result_none_while_still_polling() -> None:
    # Result present but event not set => still polling => caller must fall back.
    _reset_prefetch()
    di._PREFETCH_RESULT = {"stale": True}
    assert di.get_prefetched_audio_result() is None


def test_start_prefetch_noop_without_sounddevice(monkeypatch) -> None:
    _reset_prefetch()
    monkeypatch.setattr(di, "_get_sd", lambda: None)
    assert di.start_audio_device_prefetch() is None
    assert di.get_prefetched_audio_result() is None


def test_prefetch_publishes_settled_result(monkeypatch) -> None:
    _reset_prefetch()
    sentinel = {"available": True, "device_count": 7, "stable": True}
    monkeypatch.setattr(di, "_get_sd", lambda: object())  # sounddevice "present"
    # The prefetch thread calls the impl directly (the public function would
    # join the prefetch's own in-flight event — BUG-058 single-flight).
    monkeypatch.setattr(di, "_poll_until_stable", lambda: sentinel)

    thread = di.start_audio_device_prefetch()
    assert thread is not None
    thread.join(timeout=2)

    assert di.get_prefetched_audio_result() == sentinel
    _reset_prefetch()


def test_prefetch_swallows_settle_failure(monkeypatch) -> None:
    _reset_prefetch()

    def _boom() -> dict:
        raise RuntimeError("portaudio exploded mid-scan")

    monkeypatch.setattr(di, "_get_sd", lambda: object())
    monkeypatch.setattr(di, "_poll_until_stable", _boom)

    thread = di.start_audio_device_prefetch()
    assert thread is not None
    thread.join(timeout=2)

    # Event set, result None => caller falls back to a fresh settle (today's path).
    assert di.get_prefetched_audio_result() is None
    _reset_prefetch()
