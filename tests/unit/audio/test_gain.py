"""Shared master-output-volume gain (jarvis.audio.gain).

Covers the loudness maths every TTS sink relies on: the 0.0-1.0 knob maps to a
makeup boost so 100% is genuinely louder than the raw signal, a soft-knee
limiter keeps the boost from clipping, the unity point is byte-identical, and
attenuation below unity is a plain linear multiply. The int16 wrapper (browser +
telephony) must match the float path and never overflow.
"""
from __future__ import annotations

import numpy as np

from jarvis.audio import gain


def _rms(a) -> float:
    a = np.asarray(a, dtype=np.float64)
    return float(np.sqrt(np.mean(a * a))) if a.size else 0.0


def test_clamp_volume_bounds_and_bad_input():
    assert gain.clamp_volume(1.5) == 1.0
    assert gain.clamp_volume(-0.3) == 0.0
    assert gain.clamp_volume(0.4) == 0.4
    assert gain.clamp_volume("nonsense") == 1.0  # non-numeric → full, never mute


def test_effective_gain_scale():
    unity = 1.0 / gain._MAKEUP_GAIN
    assert gain.effective_gain(1.0) == gain._MAKEUP_GAIN     # 100% = loudest
    assert gain.effective_gain(unity) == 1.0                 # unity point
    assert gain.effective_gain(0.0) == 0.0                   # silent


def test_unity_is_byte_identical():
    unity = 1.0 / gain._MAKEUP_GAIN
    arr = np.linspace(-0.5, 0.5, 500, dtype=np.float32)
    out = gain.apply_output_gain(arr, unity)
    assert out is arr  # exact same object → no copy, no change


def test_boost_makes_quiet_speech_louder_without_clipping():
    # Quiet TTS-like signal: loud-ish peaks + a quiet body, all below full scale.
    sig = np.concatenate([
        np.full(1000, 0.30, np.float32),
        np.full(1000, 0.08, np.float32),
    ])
    boosted = gain.apply_output_gain(sig, 1.0)  # 100%
    assert _rms(boosted) > _rms(sig) * 2          # clearly louder
    assert float(np.max(np.abs(boosted))) <= 1.0  # soft limiter → never clips


def test_attenuation_below_unity_is_linear():
    unity = 1.0 / gain._MAKEUP_GAIN
    arr = np.full(200, 0.4, np.float32)
    out = gain.apply_output_gain(arr, unity / 2)  # half of unity → 0.5x
    assert np.allclose(out, 0.2, atol=1e-4)


def test_soft_limit_transparent_below_knee_and_bounded_above():
    below = np.full(100, gain._LIMIT_KNEE * 0.5, np.float32)
    assert np.allclose(gain.soft_limit(below), below)  # transparent
    # A moderate over-knee value is compressed but stays strictly below 1.0.
    mod = np.full(100, 1.5, np.float32)
    assert float(np.max(np.abs(gain.soft_limit(mod)))) < 1.0
    # An extreme value asymptotes to (but never exceeds) full scale.
    huge = np.full(100, 50.0, np.float32)
    assert float(np.max(np.abs(gain.soft_limit(huge)))) <= 1.0


def test_pcm16_boost_matches_float_and_never_overflows():
    # 0.2 full-scale int16 tone, boosted at 100%.
    sig = np.full(2000, 0.2, np.float32)
    pcm = (sig * 32768).astype(np.int16).tobytes()
    out = gain.apply_output_gain_pcm16(pcm, 1.0)
    arr = np.frombuffer(out, dtype=np.int16)
    assert arr.dtype == np.int16
    assert arr.max() <= 32767 and arr.min() >= -32768   # in range
    assert _rms(arr.astype(np.float32) / 32768.0) > _rms(sig) * 2  # louder


def test_pcm16_unity_and_empty_short_circuit():
    unity = 1.0 / gain._MAKEUP_GAIN
    pcm = (np.full(100, 0.3, np.float32) * 32768).astype(np.int16).tobytes()
    assert gain.apply_output_gain_pcm16(pcm, unity) == pcm  # unity → same bytes
    assert gain.apply_output_gain_pcm16(b"", 1.0) == b""    # empty → empty
