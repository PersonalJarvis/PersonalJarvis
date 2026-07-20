"""Unit tests for the audio hot-swap topology watcher (BUG-102).

Pins the three contracts: the signature reacts to physical changes but not to
index shuffles, the watcher refreshes exactly once per settled change and
fails open on probe outages, and a refresh quiesces every registered stream
before PortAudio is re-initialized.
"""
from __future__ import annotations

import asyncio
import sys
import time

import pytest

import jarvis.audio.topology as topology
from jarvis.audio.capture import MicrophoneCapture


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
