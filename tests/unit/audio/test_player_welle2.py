"""Unit tests for Welle-2 AudioPlayer hardening (2026-05-16).

Background: After the Welle-1 persistent-stream fix landed, the user reported
new symptoms — audible crackling ("knistert") and gradual slowdown ("immer
langsamer") — that emerged from two latent issues that Welle-1 didn't address:

1. ``sd.OutputStream(latency="high")`` is a DEVICE-HINT, not a buffer-size
   specification. On modern USB headsets it resolves to ~10 ms — far too
   small to absorb the inter-sentence gaps in the streaming-TTS pipeline.
   Buffer drained → PortAudio inserted silence (crackling) and the device
   timeline drifted (perceived slowdown).
2. ``stream.write()`` returns ``True`` when a mid-write underrun occurred,
   but the return value was discarded. Every PortAudio underrun was
   invisible at log level — diagnose-blindness.
3. The samplerate-cascade in ``_open_output_stream`` re-ran on every fresh
   stream open. The pipeline calls ``player.stop()`` at end-of-turn (for
   barge-in support), so every turn paid the cascade cost. Log spam: 17
   "OutputStream @ 24000Hz failed" warnings per hour.

The Welle-2 fixes:
  - Explicit ``latency=0.2`` (200 ms) instead of the "high" hint
  - Capture ``stream.write()`` return value and log on True
  - ``_device_rate_cache`` mapping ``source_rate -> working device_rate`` so
    subsequent opens skip the cascade

These tests pin the contracts.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from unittest.mock import MagicMock

import numpy as np
import pytest

from jarvis.audio.player import AudioPlayer
from jarvis.core.protocols import AudioChunk


async def _one_chunk(pcm: bytes, sample_rate: int = 24_000) -> AsyncIterator[AudioChunk]:
    yield AudioChunk(pcm=pcm, sample_rate=sample_rate, timestamp_ns=0, channels=1)


def _make_player_bare() -> AudioPlayer:
    """Construct an AudioPlayer without touching real sounddevice."""
    player = AudioPlayer.__new__(AudioPlayer)
    player._device = None
    player._sample_rate = 24_000
    player._channels = 1
    player._device_logged = True
    player._bus = None
    player._play_lock = None
    player._active_stream = None
    player._active_source_rate = None
    player._active_device_rate = None
    player._device_rate_cache = {}
    return player


@pytest.mark.asyncio
async def test_device_rate_cache_remembers_working_rate(monkeypatch) -> None:
    """Once ``_open_output_stream`` finds a working rate for a given
    source_rate on this device, the next open MUST skip the cascade and
    go straight to the cached rate. This is the fix for the "17 warnings
    per hour" cascade-spam after every turn-end stop().
    """
    player = _make_player_bare()

    # We don't run real PortAudio — instead spy on cache state by calling
    # _open_output_stream against a sounddevice that raises on the first
    # rate and accepts the second, then again to prove cache-hit shortcut.
    import jarvis.audio.player as player_mod

    open_call_log: list[int] = []

    class FakeStream:
        latency = 0.2

        def start(self):
            pass

    fake_query = lambda dev: {"default_samplerate": 48000}
    monkeypatch.setattr(player_mod.sd, "query_devices", fake_query)

    real_OutputStream = player_mod.sd.OutputStream

    def fake_outputstream(*, samplerate, **kw):
        open_call_log.append(samplerate)
        if samplerate == 24000:
            # Simulate WASAPI rejecting 24kHz with -9997
            raise player_mod.sd.PortAudioError(
                "Error opening OutputStream: Invalid sample rate [PaErrorCode -9997]"
            )
        # Higher rates succeed
        return FakeStream()

    monkeypatch.setattr(player_mod.sd, "OutputStream", fake_outputstream)

    # First call: should try 24000 (fail) then 48000 (succeed).
    stream1, rate1 = player._open_output_stream(24000)
    assert rate1 == 48000
    # H3 (2026-05-18): cache key is now (device, source_rate). With
    # self._device == None (bare player default), the key is (None, 24000).
    assert player._device_rate_cache[(None, 24000)] == 48000
    assert open_call_log == [24000, 48000], (
        f"first call should walk the cascade — got {open_call_log}"
    )

    # Second call: cache hit — should jump straight to 48000, NO 24000 attempt.
    open_call_log.clear()
    stream2, rate2 = player._open_output_stream(24000)
    assert rate2 == 48000
    assert open_call_log == [48000], (
        f"cached call should skip the failing 24000Hz attempt — got {open_call_log}"
    )


def test_write_samples_logs_warning_on_underflow(monkeypatch, caplog) -> None:
    """When PortAudio reports an underflow during stream.write(), the
    player must emit a WARNING — previously the bool was discarded and
    every underrun was invisible.
    """
    player = _make_player_bare()

    # Stream stub whose write() reports underflowed=True.
    class UnderflowingStream:
        latency = 0.2
        write_calls: list = []

        def write(self, arr):
            self.write_calls.append(arr.shape)
            return True  # underflow happened

    stream = UnderflowingStream()

    # 100ms of 24kHz silence, mono int16.
    arr = np.zeros(2400, dtype=np.int16)

    with caplog.at_level(logging.WARNING, logger="jarvis.audio.player"):
        player._write_samples(stream, arr, source_rate=24000, device_rate=24000)

    assert len(stream.write_calls) == 1, "stream.write must be called exactly once"
    underflow_logs = [r for r in caplog.records if "underflow" in r.getMessage().lower()]
    assert len(underflow_logs) == 1, (
        f"expected exactly 1 underflow warning, got {len(underflow_logs)}: "
        f"{[r.getMessage() for r in caplog.records]}"
    )
    msg = underflow_logs[0].getMessage()
    assert "frames=2400" in msg, f"warning should include frame count, got: {msg}"
    assert "source=24000Hz" in msg, f"warning should include source rate, got: {msg}"
    assert "device=24000Hz" in msg, f"warning should include device rate, got: {msg}"


def test_write_samples_silent_when_no_underflow(monkeypatch, caplog) -> None:
    """Inverse of the above — when stream.write() reports underflowed=False,
    NO warning is emitted (avoid log spam on normal operation).
    """
    player = _make_player_bare()

    class HealthyStream:
        def write(self, arr):
            return False  # no underflow

    arr = np.zeros(2400, dtype=np.int16)
    with caplog.at_level(logging.WARNING, logger="jarvis.audio.player"):
        player._write_samples(HealthyStream(), arr, source_rate=24000, device_rate=24000)

    underflow_logs = [r for r in caplog.records if "underflow" in r.getMessage().lower()]
    assert len(underflow_logs) == 0, "healthy write must not log"


def test_open_output_stream_uses_explicit_float_latency(monkeypatch) -> None:
    """The Welle-2 patch replaces ``latency="high"`` with ``latency=0.2``
    because the string is a device-hint that resolves to ~10ms on USB
    headsets — too small to absorb inter-sentence pipeline gaps.
    """
    player = _make_player_bare()
    import jarvis.audio.player as player_mod

    captured_kwargs: dict = {}

    class FakeStream:
        latency = 0.2
        def start(self): pass

    def fake_outputstream(**kw):
        captured_kwargs.update(kw)
        return FakeStream()

    monkeypatch.setattr(player_mod.sd, "query_devices",
                        lambda d: {"default_samplerate": 48000})
    monkeypatch.setattr(player_mod.sd, "OutputStream", fake_outputstream)

    player._open_output_stream(24000)

    assert "latency" in captured_kwargs
    latency_val = captured_kwargs["latency"]
    assert isinstance(latency_val, float), (
        f"latency must be an explicit float (not the 'high' hint that resolves "
        f"to ~10ms on USB), got {type(latency_val).__name__}={latency_val!r}"
    )
    assert latency_val >= 0.1, (
        f"latency must reserve at least 100ms buffer to absorb pipeline gaps, "
        f"got {latency_val}s"
    )
