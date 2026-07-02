"""AudioPlayer master output volume — applied in _write_samples via jarvis.audio.gain.

Drives the REAL _write_samples with a capturing fake stream (no PortAudio), so
these assert the exact samples that would reach the speaker. The 0.0-1.0 knob
maps through a makeup boost + soft limiter: unity (byte-identical) sits at
1/_MAKEUP_GAIN, 100% is a loud boost that never clips, below unity attenuates,
and the visualizer is fed the PRE-gain loudness so the orb still shows speech.
"""
from __future__ import annotations

import numpy as np

from jarvis.audio import gain, level_tap
from jarvis.audio import player as P

_UNITY = 1.0 / gain._MAKEUP_GAIN  # knob value that yields a 1:1 gain


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


def test_unity_knob_is_unattenuated():
    level_tap.reset()  # no subscriber → feed path off
    out = _play(_UNITY, 20000)
    assert np.allclose(out, 20000 / 32768.0, atol=1e-4)


def test_full_volume_boosts_without_clipping():
    level_tap.reset()
    amp = 8000  # quiet-ish source (0.244 full-scale)
    out = _play(1.0, amp)
    raw = amp / 32768.0
    assert float(np.max(np.abs(out))) > raw * 1.5   # clearly louder
    assert float(np.max(np.abs(out))) <= 1.0        # soft limiter → never clips


def test_below_unity_attenuates():
    level_tap.reset()
    out = _play(_UNITY / 2, 20000)  # half of unity → 0.5x
    assert np.allclose(out, 0.5 * 20000 / 32768.0, atol=1e-4)


def test_zero_volume_is_silent():
    level_tap.reset()
    out = _play(0.0, 20000)
    assert np.allclose(out, 0.0, atol=1e-6)


def test_out_of_range_volume_is_clamped():
    pl = P.AudioPlayer.__new__(P.AudioPlayer)
    pl.set_volume(5.0)
    assert pl._volume == 1.0
    pl.set_volume(-2.0)
    assert pl._volume == 0.0


def test_visualizer_sees_pre_gain_loudness_when_boosted(monkeypatch):
    """At any volume the equalizer/orb tracks the raw speech loudness (pre-gain),
    so the bars are not pumped up by the boost nor shrunk by attenuation.

    We capture the RAW value handed to level_tap.feed (before its normalizer) so
    the assertion is on the pre/post-gain choice, not on the normalizer's output.
    """
    fed: list[float] = []
    monkeypatch.setattr(level_tap, "has_subscribers", lambda: True)
    monkeypatch.setattr(level_tap, "feed", lambda v: fed.append(v))
    _play(1.0, 20000)  # 100% → boosted output (~1.0 peak)
    raw_rms = 20000 / 32768.0  # ~0.61
    # Every fed value is the raw signal RMS, NOT the boosted/limited output.
    assert fed and all(abs(v - raw_rms) < 1e-3 for v in fed)
