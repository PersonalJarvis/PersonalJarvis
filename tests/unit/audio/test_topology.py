"""Unit tests for the audio hot-swap topology watcher (BUG-102).

Pins the three contracts: the signature reacts to physical changes but not to
index shuffles, the watcher refreshes exactly once per settled change and
fails open on probe outages, and a refresh quiesces every registered stream
before PortAudio is re-initialized.
"""
from __future__ import annotations

import asyncio
import sys
import threading
import time

import numpy as np
import pytest

import jarvis.audio.topology as topology
from jarvis.audio.capture import MicrophoneCapture
from jarvis.audio.player import AudioPlayer


@pytest.fixture(autouse=True)
def _isolated_native_handles(monkeypatch):
    # Other audio tests deliberately return fake streams without closing them.
    monkeypatch.setattr(topology, "_native_streams", {})


def _tables(names, default_in=0, default_out=0):
    devices = [
        {"name": name, "max_input_channels": 1, "max_output_channels": 2}
        for name in names
    ]
    return devices, [], (default_in, default_out)


def test_signature_ignores_index_shuffle_but_sees_devices_and_defaults() -> None:
    base = topology.topology_signature(
        _tables(["Mic A", "Speakers B"], default_in=0, default_out=1)
    )
    # Same physical devices and defaults at shuffled indices → same identity.
    shuffled = topology.topology_signature(
        _tables(["Speakers B", "Mic A"], default_in=1, default_out=0)
    )
    assert base == shuffled
    # The OS default moving to another device IS a change (plug-in switches
    # the default output) even when the device list itself is unchanged.
    default_moved = topology.topology_signature(
        _tables(["Mic A", "Speakers B"], default_in=0, default_out=0)
    )
    assert base != default_moved
    unplugged = topology.topology_signature(_tables(["Speakers B"]))
    assert base != unplugged
    assert topology.topology_signature(None) is None


@pytest.mark.asyncio
async def test_watcher_refreshes_once_per_settled_change(monkeypatch) -> None:
    monkeypatch.setattr(topology, "_SETTLE_S", 0.0)
    signatures = iter(["a", "a", "b", "b", "b", "b", "b"])
    refreshes: list[float] = []

    task = asyncio.create_task(
        topology.watch_topology(
            player=None,
            poll_s=0.01,
            probe=lambda: next(signatures, "b"),
            refresh=lambda: refreshes.append(time.monotonic()) or True,
        )
    )
    await asyncio.sleep(0.3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(refreshes) == 1


@pytest.mark.asyncio
async def test_a_device_loss_signal_probes_without_waiting_for_the_poll() -> None:
    """The steady-state poll is deliberately slow, so the signals that mean
    "a device may be gone" (a mic stall, a failed stream open) must be able to
    skip the wait. Without this, raising the interval would simply make
    hot-plug recovery that much slower — each probe is a full Python +
    PortAudio subprocess, which is why the interval has to stay cheap.
    """
    probes: list[float] = []
    task = asyncio.create_task(
        topology.watch_topology(
            player=None,
            poll_s=60.0,  # would never fire inside this test on its own
            probe=lambda: probes.append(time.monotonic()) or "sig",
            refresh=lambda: True,
        )
    )
    try:
        await asyncio.sleep(0.05)
        assert probes == [], "the slow poll must not have fired yet"

        topology.request_topology_probe()
        await asyncio.sleep(0.05)
        assert len(probes) == 1, (
            "a device-loss signal must trigger an immediate probe instead of "
            "waiting out the poll interval"
        )

        # The request is consumed, not sticky — otherwise the watcher would
        # spin at full speed forever after one stall.
        await asyncio.sleep(0.05)
        assert len(probes) == 1, "the request must be one-shot"
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_request_probe_is_a_noop_with_no_watcher_running() -> None:
    """Called from capture's error paths, which run in tests and headless
    installs where no watcher exists. It must never raise there."""
    topology._probe_request = None
    topology._probe_loop = None
    topology.request_topology_probe()  # must not raise


@pytest.mark.asyncio
async def test_watcher_fails_open_when_probe_returns_none(monkeypatch) -> None:
    monkeypatch.setattr(topology, "_SETTLE_S", 0.0)
    refreshes: list[bool] = []
    task = asyncio.create_task(
        topology.watch_topology(
            player=None,
            poll_s=0.01,
            probe=lambda: None,
            refresh=lambda: refreshes.append(True) or True,
        )
    )
    await asyncio.sleep(0.15)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert refreshes == []


class _FakeSd:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def _terminate(self) -> None:
        self.calls.append("terminate")

    def _initialize(self) -> None:
        self.calls.append("initialize")


class _FakePlayer:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    def invalidate_device_cache(self) -> None:
        self._calls.append("player-invalidate")

    def set_device(self, device) -> None:
        self._calls.append(f"player-set:{device}")


class _FakeCapture:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    def discard_native_stream(self) -> None:
        self._calls.append("capture-discard")


def test_refresh_quiesces_streams_before_reinit(monkeypatch) -> None:
    calls: list[str] = []
    fake_sd = _FakeSd()
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)
    import jarvis.audio.capture as capture_module

    monkeypatch.setattr(
        capture_module,
        "_invalidate_resolve_cache",
        lambda: calls.append("resolve-invalidate"),
    )
    fake_capture = _FakeCapture(calls)
    topology.register_capture(fake_capture)
    try:
        ok = topology.refresh_audio_backend(_FakePlayer(calls), "auto-headset")
    finally:
        topology.unregister_capture(fake_capture)

    assert ok is True
    # Native streams are quiesced BEFORE the re-init, the caches after it.
    assert calls == [
        "capture-discard",
        "player-invalidate",
        "resolve-invalidate",
        "player-set:auto-headset",
    ]
    assert fake_sd.calls == ["terminate", "initialize"]


def test_capture_discard_backdates_watchdog_heartbeat() -> None:
    class _Stream:
        def __init__(self) -> None:
            self.aborted = False
            self.closed = False

        def abort(self) -> None:
            self.aborted = True

        def close(self) -> None:
            self.closed = True

    capture = MicrophoneCapture.__new__(MicrophoneCapture)
    stream = _Stream()
    capture._stream = stream
    capture._device_spec = "auto-headset"
    capture._preferred_device = 7  # stale resolved index from the old table
    capture._last_chunk_monotonic = time.monotonic()

    capture.discard_native_stream()

    assert capture._stream is None
    assert stream.aborted and stream.closed
    # A non-pinned spec re-enters fresh name/auto resolution on reopen.
    assert capture._preferred_device == "auto-headset"
    # Backdated past the stall threshold → the watchdog reopens on its next tick.
    assert (
        time.monotonic() - capture._last_chunk_monotonic
        > MicrophoneCapture._STALL_THRESHOLD_S
    )


@pytest.mark.asyncio
async def test_watcher_retries_deferred_refresh_without_another_change(monkeypatch):
    monkeypatch.setattr(topology, "_SETTLE_S", 0.0)
    signatures = iter(["a", "b"])
    attempts = []
    completed = asyncio.Event()
    loop = asyncio.get_running_loop()

    def refresh():
        attempts.append(True)
        if len(attempts) == 1:
            return False
        loop.call_soon_threadsafe(completed.set)
        return True

    task = asyncio.create_task(topology.watch_topology(
        None, poll_s=0.005, probe=lambda: next(signatures, "b"), refresh=refresh,
    ))
    try:
        await asyncio.wait_for(completed.wait(), 2)
        await asyncio.sleep(0.03)
        assert len(attempts) == 2
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_watcher_ignores_change_that_disappears_during_settle(monkeypatch):
    monkeypatch.setattr(topology, "_SETTLE_S", 0.0)
    signatures = iter(["a", "b", "a"])
    refreshes = []
    task = asyncio.create_task(topology.watch_topology(
        None, poll_s=0.005, probe=lambda: next(signatures, "a"),
        refresh=lambda: refreshes.append(True) or True,
    ))
    try:
        await asyncio.sleep(0.1)
        assert refreshes == []
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


def _idle_player():
    player = AudioPlayer.__new__(AudioPlayer)
    player._play_lock = None
    player._active_stream = None
    player._device_rate_cache = {}
    player._device_rate_failed = set()
    player._device = None
    return player


def test_refresh_waits_for_native_write_even_without_async_playback_owner(monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    fake_sd = _FakeSd()
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)
    player = _idle_player()

    class Stream:
        latency = 0.0

        def write(self, samples):
            entered.set()
            assert release.wait(2)
            return False

        def stop(self):
            pass

        def close(self):
            pass

    player._active_stream = Stream()
    failures = []

    def write():
        try:
            player._write_samples(player._active_stream, np.zeros(16, dtype=np.int16), 16000, 16000)
        except BaseException as exc:
            failures.append(exc)

    worker = threading.Thread(target=write)
    worker.start()
    try:
        assert entered.wait(2)
        assert topology.refresh_audio_backend(player) is False
        assert fake_sd.calls == []
    finally:
        release.set()
        worker.join(2)
    assert not worker.is_alive()
    assert failures == []
    assert topology.refresh_audio_backend(player) is True
    assert fake_sd.calls == ["terminate", "initialize"]


@pytest.mark.asyncio
async def test_refresh_defers_between_tts_chunks(monkeypatch):
    fake_sd = _FakeSd()
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)
    player = _idle_player()
    async with player._get_play_lock():
        assert topology.refresh_audio_backend(player) is False
        assert fake_sd.calls == []
    assert topology.refresh_audio_backend(player) is True


@pytest.mark.parametrize("owner", ["capture", "player"])
def test_failed_stream_close_prevents_termination(monkeypatch, owner):
    fake_sd = _FakeSd()
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)

    class UnclosableStream:
        def abort(self):
            pass

        def stop(self):
            pass

        def close(self):
            raise RuntimeError("device is still busy")

    stream = UnclosableStream()
    player = _idle_player()
    capture = MicrophoneCapture.__new__(MicrophoneCapture)
    capture._stream = stream
    if owner == "capture":
        topology.register_capture(capture)
    else:
        player._active_stream = stream
    try:
        assert topology.refresh_audio_backend(player) is False
        assert fake_sd.calls == []
        assert (capture._stream if owner == "capture" else player._active_stream) is stream
    finally:
        topology.unregister_capture(capture)


def test_unpublished_or_detached_native_stream_prevents_termination(monkeypatch):
    fake_sd = _FakeSd()
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)
    stream = object()
    with topology.stream_open_guard():
        topology.register_native_stream(stream)
    assert topology.refresh_audio_backend(None) is False
    assert fake_sd.calls == []
    topology.unregister_native_stream(stream)
    assert topology.refresh_audio_backend(None) is True
