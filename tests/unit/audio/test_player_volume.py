"""AudioPlayer master output volume — a 0.0–1.0 gain applied in _write_samples.

Drives the REAL _write_samples with a capturing fake stream (no PortAudio), so
these assert the exact samples that would reach the speaker. Covers: full volume
is byte-identical (unattenuated), fractional volume scales the amplitude, zero is
silent, out-of-range is clamped, and — crucially — the visualizer (level_tap)
keeps seeing the PRE-gain loudness so the orb/equalizer still shows speech when
Jarvis is turned down.
"""
from __future__ import annotations

import numpy as np

from jarvis.audio import level_tap
from jarvis.audio import player as P


class _CapturingStream:
    """Stand-in for sd.OutputStream that records every written sub-block."""

    def __init__(self) -> None:
        self.writes: list[np.ndarray] = []

    def write(self, arr) -> bool:
        self.writes.append(np.asarray(arr).copy())
        return False  # no underflow


def _bare_player(volume: float) -> P.AudioPlayer:
    pl = P.AudioPlayer.__new__(P.AudioPlayer)  # bypass device init
    pl._volume = volume
    pl._init_progress()
    return pl


def _play(volume: float, amplitude: int, n: int = 4000) -> np.ndarray:
    """Run _write_samples for a constant-amplitude mono int16 signal, return the
    concatenated float32 stereo samples handed to the stream."""
    pl = _bare_player(volume)
    stream = _CapturingStream()
    arr = np.full(n, amplitude, dtype=np.int16)
    pl._write_samples(stream, arr, 24000, 24000)  # source_rate == device_rate
    return np.concatenate(stream.writes, axis=0)


def test_full_volume_is_unattenuated():
    level_tap.reset()  # no subscriber → feed path off
    out = _play(1.0, 20000)
    assert np.allclose(out, 20000 / 32768.0, atol=1e-4)


def test_half_volume_halves_amplitude():
    level_tap.reset()
    out = _play(0.5, 20000)
    assert np.allclose(out, 0.5 * 20000 / 32768.0, atol=1e-4)


def test_zero_volume_is_silent():
    level_tap.reset()
    out = _play(0.0, 20000)
    assert np.allclose(out, 0.0, atol=1e-6)


def test_out_of_range_volume_is_clamped_to_full():
    # A >1.0 gain would clip; the player clamps on construction, but even a
    # raw over-driven value here must not amplify past the input.
    level_tap.reset()
    pl = P.AudioPlayer.__new__(P.AudioPlayer)
    pl.set_volume(5.0)  # clamped to 1.0
    assert pl._volume == 1.0


def test_low_volume_keeps_visualizer_full_scale():
    """At 10% volume the audio is attenuated but the equalizer/orb still sees
    the pre-gain RMS — so the user sees Jarvis is speaking even when quiet."""
    level_tap.reset()
    got: list[float] = []
    level_tap.subscribe(got.append)
    try:
        out = _play(0.1, 30000)
    finally:
        level_tap.reset()
    # Audio is attenuated to ~10%.
    assert np.allclose(out, 0.1 * 30000 / 32768.0, atol=1e-4)
    # But the visualizer saw near-full-scale loudness (30000/32768 ≈ 0.92).
    assert got and max(got) > 0.5
