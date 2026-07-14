"""PortAudio re-init must be serialized and single-flight (BUG-058).

The boot prefetch (``start_audio_device_prefetch``) and the pipeline's
Phase-A ``_stabilize_audio_devices`` both call
``wait_for_stable_audio_devices``; when the pipeline arrives while the
prefetch is still polling, two threads used to interleave
``sd._terminate()`` / ``sd._initialize()``. Windows WASAPI tolerates that;
macOS CoreAudio's HAL answers concurrent teardown/re-init with a NATIVE
fault ("Python quit unexpectedly") — it fires seconds after the window
appears, right as first-launch onboarding starts. Two defenses:

1. ``_refresh_device_count`` holds a module lock around the whole
   terminate → initialize → query sequence (hard safety).
2. ``wait_for_stable_audio_devices`` JOINS an in-flight prefetch instead
   of starting a second concurrent poll loop (no duplicate re-init work).
"""
from __future__ import annotations

import threading

from jarvis.audio import device_init


class _ReentrancySD:
    """Fake sounddevice that records overlapping re-init sequences."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._inside = 0
        self.max_concurrency = 0

    def _enter(self) -> None:
        with self._lock:
            self._inside += 1
            self.max_concurrency = max(self.max_concurrency, self._inside)

    def _exit(self) -> None:
        with self._lock:
            self._inside -= 1

    def _terminate(self) -> None:
        self._enter()

    def _initialize(self) -> None:
        pass

    def query_devices(self):
        try:
            return [{"max_input_channels": 1, "max_output_channels": 0}]
        finally:
            self._exit()


def test_refresh_device_count_serializes_reinit_across_threads() -> None:
    sd = _ReentrancySD()
    errors: list[BaseException] = []

    def _hammer() -> None:
        try:
            for _ in range(50):
                device_init._refresh_device_count(sd)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=_hammer) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert errors == []
    assert sd.max_concurrency == 1  # never two re-init sequences interleaved


def test_wait_joins_inflight_prefetch_instead_of_polling(monkeypatch) -> None:
    event = threading.Event()
    monkeypatch.setattr(device_init, "_PREFETCH_EVENT", event)
    monkeypatch.setattr(device_init, "_PREFETCH_STARTED", True)
    polls: list[str] = []
    monkeypatch.setattr(
        device_init,
        "_poll_until_stable",
        lambda **_kw: polls.append("poll") or {"available": True},
    )
    sentinel = {"available": True, "device_count": 7, "stable": True}

    def _finish_prefetch() -> None:
        device_init._PREFETCH_RESULT = sentinel
        event.set()

    monkeypatch.setattr(device_init, "_PREFETCH_RESULT", None)
    t = threading.Timer(0.05, _finish_prefetch)
    t.start()
    try:
        result = device_init.wait_for_stable_audio_devices(max_wait_s=5.0)
    finally:
        t.cancel()
    assert result is sentinel  # joined the prefetch...
    assert polls == []  # ...and never started a second concurrent poll loop


def test_wait_polls_itself_when_no_prefetch_running(monkeypatch) -> None:
    monkeypatch.setattr(device_init, "_PREFETCH_EVENT", threading.Event())
    monkeypatch.setattr(device_init, "_PREFETCH_STARTED", False)
    sentinel = {"available": True, "device_count": 3, "stable": True}
    monkeypatch.setattr(device_init, "_poll_until_stable", lambda **_kw: sentinel)
    assert device_init.wait_for_stable_audio_devices() is sentinel
