"""Unit contract for the shared, level-independent wake input normalizer.

Root cause of "the wake word only triggers when I shout" (mission 2026-06-30):
the microphone capture applies NO normalization, and each wake engine gated
detection on ABSOLUTE amplitude with its own fixed thresholds. On a quiet mic (a
laptop's built-in mic, a low input-gain headset) a normal-volume utterance never
reached the level the detector expected, so only a shout crossed the bar.

``AdaptiveWakeNormalizer`` is the single, reusable input AGC every wake path can
share. It removes absolute loudness as the gate: it tracks the ambient noise
floor (EMA over quiet frames, the same idea as ``mic_level.LevelNormalizer``) and
amplifies a signal that rises a margin ABOVE that floor toward a target peak,
capped and amplify-only. A quiet-but-real utterance is lifted into the band the
detector expects; flat digital silence and steady sub-floor hiss (which never
rise above their own tracked floor) are left unamplified, so the AGC can never
manufacture an ambient false-fire band. Pure numpy — no I/O, no model, no
OS-specific code — so it behaves identically on Windows, Linux and macOS.
"""
from __future__ import annotations

import numpy as np

from jarvis.audio.wake_normalizer import AdaptiveWakeNormalizer


def _sine_int16(peak: float, n: int = 1280) -> np.ndarray:
    """A 440 Hz sine of ``n`` int16 samples whose float peak is ``peak`` (0..1)."""
    t = np.arange(n, dtype=np.float32)
    wave = np.sin(2.0 * np.pi * 440.0 * t / 16_000.0) * float(peak)
    return (np.clip(wave, -1.0, 1.0) * 32767.0).astype(np.int16)


def _float_peak(frame: np.ndarray) -> float:
    return float(np.max(np.abs(frame.astype(np.float32) / 32768.0)))


# ---------------------------------------------------------------------------
# Level independence — the whole point.
# ---------------------------------------------------------------------------

def test_same_utterance_at_three_levels_normalizes_into_a_common_band() -> None:
    """A loud, a normal, and a quiet copy of the same utterance all leave the
    normalizer at a comparable level — absolute loudness is no longer the gate."""
    outs = []
    for peak in (0.6, 0.06, 0.015):
        norm = AdaptiveWakeNormalizer()
        out = norm.process(_sine_int16(peak))
        outs.append(_float_peak(out))
    for peak_in, peak_out in zip((0.6, 0.06, 0.015), outs, strict=True):
        assert 0.4 <= peak_out <= 0.98, (
            f"input peak {peak_in} normalized to {peak_out:.3f}, outside the band"
        )
    # And the three normalized levels are close to each other (level-independent).
    assert max(outs) - min(outs) <= 0.35


def test_flat_digital_silence_is_returned_unchanged() -> None:
    norm = AdaptiveWakeNormalizer()
    frame = np.zeros(1280, dtype=np.int16)
    out = norm.process(frame)
    assert np.array_equal(out, frame)


def test_steady_subfloor_hiss_is_not_lifted_into_the_speech_band() -> None:
    # A fresh normalizer must treat a sub-floor frame (idle hiss) as silence and
    # leave it quiet, so the AGC cannot manufacture an ambient false-fire band.
    norm = AdaptiveWakeNormalizer()
    out = norm.process(_sine_int16(peak=0.004))
    assert _float_peak(out) < 0.05


def test_loud_input_is_never_attenuated() -> None:
    norm = AdaptiveWakeNormalizer()
    out = norm.process(_sine_int16(peak=0.8))
    assert _float_peak(out) >= 0.79


def test_gain_is_capped() -> None:
    # cap = 6 dB == 2x: a 0.05-peak frame reaches ~0.10, NOT the -3 dBFS target a
    # >10x uncapped gain would produce.
    norm = AdaptiveWakeNormalizer(max_gain_db=6.0)
    out = norm.process(_sine_int16(peak=0.05))
    assert 0.05 * 2 * 0.9 <= _float_peak(out) <= 0.05 * 2 * 1.05


# ---------------------------------------------------------------------------
# Adaptive floor — the quiet-mic unlock and the noisy-room guard.
# ---------------------------------------------------------------------------

def test_adaptive_floor_unlocks_a_very_quiet_wake_on_a_quiet_mic() -> None:
    """On a quiet mic the ambient floor settles low, so a genuinely quiet wake
    (peak 0.008 — below the legacy fixed 0.02 floor that silently dropped it)
    rises above the adapted floor and IS amplified. This is the core fix."""
    norm = AdaptiveWakeNormalizer()
    # Simulate a quiet room: sustained near-silent hiss lets the floor adapt down.
    for _ in range(60):
        norm.process(_sine_int16(peak=0.0015))
    out = norm.process(_sine_int16(peak=0.008))
    assert _float_peak(out) > 0.008 * 3.0, (
        f"a quiet wake on a quiet mic must be amplified, got {_float_peak(out):.3f}"
    )
    assert norm.speech_present


def test_fresh_normalizer_keeps_the_legacy_subfloor_guard() -> None:
    # Before any adaptation a 0.008 frame is below the start floor's speech
    # threshold and must NOT be amplified (mirrors the OWW subfloor guard so the
    # first wake in a session cannot false-fire on idle hiss).
    norm = AdaptiveWakeNormalizer()
    out = norm.process(_sine_int16(peak=0.008))
    assert _float_peak(out) < 0.05
    assert not norm.speech_present


def test_speech_present_is_level_independent() -> None:
    norm = AdaptiveWakeNormalizer()
    norm.process(_sine_int16(peak=0.2))
    assert norm.speech_present
    norm.reset()
    norm.process(np.zeros(1280, dtype=np.int16))
    assert not norm.speech_present


def test_reset_clears_the_envelope_state() -> None:
    norm = AdaptiveWakeNormalizer()
    norm.process(_sine_int16(peak=0.8))  # arm a high rolling peak
    norm.reset()
    out = norm.process(_sine_int16(peak=0.06))
    assert _float_peak(out) > 0.06 * 1.5


def test_pure_numpy_no_side_channels() -> None:
    # A defensive smoke test: process returns an int16 array of the same length
    # and never mutates its input (the caller reuses the buffer).
    norm = AdaptiveWakeNormalizer()
    frame = _sine_int16(peak=0.05)
    original = frame.copy()
    out = norm.process(frame)
    assert out.dtype == np.int16
    assert out.shape == frame.shape
    assert np.array_equal(frame, original), "process must not mutate its input"
