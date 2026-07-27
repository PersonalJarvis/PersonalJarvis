"""AudioPlayer publishes its output RMS through level_tap during playback.

Drives play_chunks with a fake chunk iterator and stubbed stream/write so no
real PortAudio device is opened; asserts the per-flush RMS reaches a subscriber.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import numpy as np

from jarvis.audio import level_tap


class _DummyLock:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeStream:
    """Stand-in for sd.OutputStream: write() accepts the samples and reports no
    underflow, so the REAL _write_samples runs and feeds the per-sub-block RMS."""

    def write(self, _arr):
        return False


def _make_player():
    from jarvis.audio import player as P

    pl = P.AudioPlayer.__new__(P.AudioPlayer)  # bypass device init
    pl._bus = None
    pl._active_stream = None
    pl._active_source_rate = None
    pl._active_device_rate = None
    pl._log_device_once = lambda: None
    pl._get_play_lock = lambda: _DummyLock()
    pl._open_output_stream = lambda rate: (_FakeStream(), rate)
    # _write_samples is NOT stubbed: it runs for real (numpy + the fake
    # stream.write) and feeds the per-sub-block RMS to level_tap, which is the
    # behaviour under test.
    return pl


async def _one_loud_chunk():
    pcm = np.full(2000, 30000, dtype=np.int16).tobytes()  # near full scale
    yield SimpleNamespace(pcm=pcm, sample_rate=24000)


async def test_player_publishes_rms_when_subscribed():
    level_tap.reset()
    got: list[float] = []
    level_tap.subscribe(got.append)
    try:
        await _make_player().play_chunks(_one_loud_chunk())
    finally:
        level_tap.reset()
    assert got, "expected at least one level sample"
    assert max(got) > 0.5  # full-scale int16 → RMS ~0.9


async def test_player_no_publish_without_subscriber():
    level_tap.reset()
    # No subscriber registered → must not raise, and the RMS is skipped.
    await _make_player().play_chunks(_one_loud_chunk())
    assert level_tap.has_subscribers() is False


class _LatentFakeStream(_FakeStream):
    """A device that reports real output latency, like Bluetooth on macOS."""

    latency = 0.2


async def test_player_delays_levels_by_the_device_output_latency():
    # write() returns at buffer-accept time; the block is HEARD one output
    # latency later. The visualizer level must follow the heard timeline: no
    # sample may reach the sink before the device latency has elapsed.
    import time

    level_tap.reset()
    got: list[tuple[float, float]] = []  # (arrival_monotonic, level)
    level_tap.subscribe(lambda lv: got.append((time.monotonic(), lv)))
    pl = _make_player()
    pl._open_output_stream = lambda rate: (_LatentFakeStream(), rate)
    t0 = time.monotonic()
    try:
        await pl.play_chunks(_one_loud_chunk())
        deadline = time.monotonic() + 2.0
        while not got and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
    finally:
        level_tap.reset()
    assert got, "expected the delayed level samples to arrive"
    first_arrival, _lv = got[0]
    assert first_arrival - t0 >= 0.15  # ~latency later, never at accept time
