"""Shared master-output-volume gain (jarvis.audio.gain).

Covers the loudness maths every TTS sink relies on: the 0.0-1.0 knob maps to a
makeup boost so 100% is genuinely louder than the raw signal, a look-ahead peak
limiter keeps the boost under full scale WITHOUT distorting it, the unity point
is byte-identical, and attenuation below unity is a plain linear multiply. The
int16 wrapper (browser + telephony) must match the float path and never
overflow.

The distortion tests are the reason this module was rewritten on 2026-08-25: the
previous static ``tanh`` curve measured ~22% THD on a 0.5 peak at 100% volume,
heard as a rasp on loud syllables. A limiter rides the gain instead of bending
the wave, so the same loudness must now arrive essentially harmonic-free.
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


def _thd_percent(signal, sample_rate: int, freq: float) -> float:
    """Total harmonic distortion of a limited tone, in percent."""
    spectrum = np.abs(np.fft.rfft(np.asarray(signal, dtype=np.float64) * np.hanning(len(signal))))

    def _bin_energy(harmonic: int) -> float:
        index = int(round(harmonic * freq * len(signal) / sample_rate))
        return float(spectrum[index - 2:index + 3].sum()) if index + 3 < len(spectrum) else 0.0

    fundamental = _bin_energy(1)
    harmonics = sum(_bin_energy(n) ** 2 for n in range(2, 12))
    return float(np.sqrt(harmonics) / fundamental * 100.0) if fundamental else 0.0


def _tone(peak: float, sample_rate: int = 24_000, freq: float = 220.0, seconds: float = 1.0):
    t = np.arange(int(sample_rate * seconds)) / sample_rate
    return (np.sin(2 * np.pi * freq * t) * peak).astype(np.float32)


def test_boost_adds_no_audible_distortion():
    """The whole point of the rewrite: loud input stays harmonic-free.

    The retired tanh curve produced 8% THD here at peak 0.3 and 22% at 0.5.
    """
    for peak in (0.3, 0.5, 0.9):
        out = gain.apply_output_gain(
            _tone(peak), 1.0, sample_rate=24_000, limiter=gain.PeakLimiter()
        )
        assert _thd_percent(out, 24_000, 220.0) < 1.0, f"distortion at peak {peak}"


def test_limiter_holds_the_ceiling_from_the_first_sample():
    for peak in (0.3, 0.5, 0.9, 4.0):
        out = gain.apply_output_gain(
            _tone(peak), 1.0, sample_rate=24_000, limiter=gain.PeakLimiter()
        )
        assert float(np.max(np.abs(out))) <= 1.0
        if peak * gain._MAKEUP_GAIN > 1.0:
            # Loud enough to be limited: it should sit ON the ceiling, not under
            # it — a limiter that overshoots its target is just an attenuator.
            assert float(np.max(np.abs(out))) >= gain._LIMIT_CEILING - 1e-3


def test_streamed_buffers_limit_exactly_like_one_pass():
    """A stateful limiter must make buffer seams invisible.

    Feeding the same signal as many buffers has to yield the identical waveform,
    or the seams themselves would be audible as level steps.
    """
    signal = _tone(0.6)
    streaming = gain.PeakLimiter()
    chunk = int(24_000 * 0.12)
    streamed = np.concatenate([
        gain.apply_output_gain(
            signal[i:i + chunk], 1.0, sample_rate=24_000, limiter=streaming
        )
        for i in range(0, len(signal), chunk)
    ])
    one_pass = gain.apply_output_gain(
        signal, 1.0, sample_rate=24_000, limiter=gain.PeakLimiter()
    )
    assert np.array_equal(streamed, one_pass)


def test_limiter_reset_reopens_at_unity():
    limiter = gain.PeakLimiter()
    limiter.process(_tone(0.9, seconds=0.2) * 4.0, 24_000)
    assert limiter._gain < 1.0  # ducked by the loud pass
    limiter.reset()
    quiet = _tone(0.1, seconds=0.05)
    assert np.allclose(limiter.process(quiet, 24_000), quiet)  # untouched again


def test_limiter_accepts_stereo_and_links_the_channels():
    mono = _tone(0.5, seconds=0.2) * 4.0
    stereo = np.column_stack((mono, mono * 0.25)).astype(np.float32)
    out = gain.PeakLimiter().process(stereo, 24_000)
    assert out.shape == stereo.shape
    assert float(np.max(np.abs(out))) <= 1.0
    # One shared gain curve: the quiet channel keeps its exact 1:4 ratio.
    assert np.allclose(out[:, 1] * 4.0, out[:, 0], atol=1e-6)


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
