"""Feed-dry stall de-click in ``AudioPlayer.play_chunks``.

Live forensic 2026-07-21 08:40 (realtime session 43cb78d0, Gemini Live):
the provider paused audio delivery 1850 ms mid-sentence. The device buffer
drained, PortAudio underflowed, and the waveform was cut at speech
amplitude — audible as a choppy mid-sentence pause with a click/crackle at
both edges. The missing audio cannot be conjured locally; the fix smooths
the gap's edges instead: after ``FEED_STALL_FADE_S`` of dry feed the player
appends a short ramp from the last written sample to zero, and the first
resumed block is faded back in.

These tests drive ``play_chunks`` with a producer that stalls mid-stream
and assert on the exact PCM handed to the writer.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import numpy as np
import pytest

import jarvis.audio.player as player_module
from jarvis.audio.player import AudioPlayer
from jarvis.core.protocols import AudioChunk


def _chunk(samples: np.ndarray, sample_rate: int = 24_000) -> AudioChunk:
    return AudioChunk(
        pcm=samples.astype(np.int16).tobytes(),
        sample_rate=sample_rate,
        timestamp_ns=0,
        channels=1,
    )


def _make_player(monkeypatch) -> tuple[AudioPlayer, list[np.ndarray]]:
    """AudioPlayer with stream IO faked; returns the arrays given to write."""
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

    written: list[np.ndarray] = []

    def fake_open(needed_rate: int):
        return (object(), needed_rate)

    def fake_write(stream, arr, src_rate, dev_rate, **_kwargs):
        written.append(np.array(arr, dtype=np.int16))

    monkeypatch.setattr(player, "_open_output_stream", fake_open)
    monkeypatch.setattr(player, "_close_output_stream", lambda stream: None)
    monkeypatch.setattr(player, "_write_samples", fake_write)
    # Fast stall detection so the test does not sleep for real buffer spans.
    monkeypatch.setattr(player_module, "FEED_STALL_FADE_S", 0.02)
    return player, written


@pytest.mark.asyncio
async def test_stall_gap_gets_fade_out_and_fade_in(monkeypatch) -> None:
    """A mid-stream feed stall must ramp the edge down to zero and ramp the
    resumed audio back in — never leave the waveform cut at speech amplitude.
    """
    player, written = _make_player(monkeypatch)
    loud = np.full(4_000, 8_000, dtype=np.int16)  # ~166 ms, ends loud

    async def stalling_feed() -> AsyncIterator[AudioChunk]:
        yield _chunk(loud)
        await asyncio.sleep(0.15)  # well past the patched 20 ms stall window
        yield _chunk(loud)

    await player.play_chunks(stalling_feed())

    assert len(written) == 3, (
        f"expected [audio, fade-out ramp, resumed audio], got {len(written)}"
    )
    ramp = written[1]
    assert ramp[0] == pytest.approx(8_000, abs=200), (
        "fade-out must continue from the last written sample"
    )
    assert ramp[-1] == 0, "fade-out must end at silence"
    assert list(np.abs(ramp)) == sorted(np.abs(ramp), reverse=True), (
        "fade-out must descend monotonically"
    )
    resumed = written[2]
    assert abs(int(resumed[0])) < 300, (
        "resumed audio must start near silence (fade-in), not at full "
        f"speech amplitude — first sample was {int(resumed[0])}"
    )
    assert resumed[-1] == 8_000, "the fade-in must not touch the block's tail"


@pytest.mark.asyncio
async def test_continuous_feed_injects_nothing(monkeypatch) -> None:
    """A healthy back-to-back feed must reach the device byte-identical —
    no ramps, no inserted silence (the pre-fix contract stays intact).
    """
    player, written = _make_player(monkeypatch)
    tone = np.full(4_000, 5_000, dtype=np.int16)

    async def steady_feed() -> AsyncIterator[AudioChunk]:
        for _ in range(3):
            yield _chunk(tone)

    await player.play_chunks(steady_feed())

    joined = np.concatenate(written)
    assert joined.tobytes() == np.tile(tone, 3).tobytes(), (
        "continuous playback must remain byte-identical — de-click ramps "
        "may only appear around a genuine feed-dry stall"
    )


@pytest.mark.asyncio
async def test_stall_on_silent_tail_skips_the_ramp(monkeypatch) -> None:
    """A waveform already ending at zero cannot click — no ramp is written."""
    player, written = _make_player(monkeypatch)
    fading = np.linspace(4_000, 0, 4_000).astype(np.int16)  # ends at 0

    async def stalling_feed() -> AsyncIterator[AudioChunk]:
        yield _chunk(fading)
        await asyncio.sleep(0.15)
        yield _chunk(np.full(4_000, 5_000, dtype=np.int16))

    await player.play_chunks(stalling_feed())

    assert len(written) == 2, (
        "no fade-out ramp may be injected when the tail is already silent"
    )
    assert abs(int(written[1][0])) < 300, (
        "the resumed block is still faded in after any stall"
    )
