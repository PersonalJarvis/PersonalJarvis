"""Unit-level guard for ``WakeGainNormalizer`` — the wake-path input AGC.

Root cause of "the wake word only triggers when I shout" (2026-06-28): the fast
OpenWakeWord detector is the sole wake path in the default lightweight config
(the RollingWhisperWake low-volume backstop is a power-user opt-in). That fast
path fed RAW int16 frames into the neural model — no gain. The model's
activation score scales with input level, so on this project's documented
quiet-mic hardware (normal-speech rms ~0.01-0.02) a genuine "Hey Jarvis" peaks
at ~0.10-0.14, just *below* the pinned 0.15 threshold. Only shouting lifted the
level over the bar.

The threshold cannot move (``test_wake_threshold`` pins the BUG-009 floor), so
the fix is the same peak normalization the RollingWhisperWake backstop already
uses, brought to the default OWW path: a lightweight, amplify-only, noise-gated,
capped streaming AGC. This file pins the mechanism in isolation; the companion
``test_openwakeword_normalization.py`` pins the provider-level outcome (a quiet
wake actually clears the threshold and fires).

Pinned behaviour:

* a quiet *above-floor* frame is amplified toward the target (so normal-volume
  speech reaches the level the model + threshold expect);
* pure silence and *sub-floor* noise are NOT amplified (no idle false-fire
  storm — the AGC-level analogue of the BUG-009 ambient guard);
* a loud frame is never attenuated (amplify-only — loud already works);
* the gain is capped (a near-silent noise floor can't be blown to full scale);
* ``reset()`` clears the rolling envelope so a stale loud burst cannot suppress
  the gain on the next quiet wake;
* the provider routes frames through the AGC before scoring, and the escape
  hatch (``gain_normalization=False``) passes raw audio unchanged.
"""
from __future__ import annotations

from collections.abc import AsyncIterator

import numpy as np

from jarvis.core.protocols import AudioChunk
from jarvis.plugins.wake.openwakeword_provider import (
    OWW_FRAME_SAMPLES,
    OpenWakeWordProvider,
    WakeGainNormalizer,
)


def _sine_int16(peak: float, n: int = OWW_FRAME_SAMPLES) -> np.ndarray:
    """A 440 Hz sine of ``n`` int16 samples whose float peak is ``peak`` (0..1)."""
    t = np.arange(n, dtype=np.float32)
    wave = np.sin(2.0 * np.pi * 440.0 * t / 16_000.0) * float(peak)
    return (np.clip(wave, -1.0, 1.0) * 32767.0).astype(np.int16)


def _float_peak(frame: np.ndarray) -> float:
    return float(np.max(np.abs(frame.astype(np.float32) / 32768.0)))


# ---------------------------------------------------------------------------
# WakeGainNormalizer — the pure mechanism
# ---------------------------------------------------------------------------

def test_quiet_above_floor_frame_is_amplified_toward_target() -> None:
    norm = WakeGainNormalizer()
    frame = _sine_int16(peak=0.06)
    out = norm.process(frame)
    in_peak = _float_peak(frame)
    out_peak = _float_peak(out)
    assert out_peak > in_peak * 1.5, (
        f"normal-volume speech must be lifted, got {in_peak:.3f} -> {out_peak:.3f}"
    )
    assert out_peak <= 0.95, "must not clip hard against full scale"


def test_pure_silence_is_returned_unchanged() -> None:
    norm = WakeGainNormalizer()
    frame = np.zeros(OWW_FRAME_SAMPLES, dtype=np.int16)
    out = norm.process(frame)
    assert np.array_equal(out, frame)


def test_subfloor_noise_is_not_amplified_into_the_speech_band() -> None:
    # Below the noise floor: idle hiss / room rumble must stay quiet so the AGC
    # cannot manufacture an ambient false-fire band (the BUG-009 guard).
    norm = WakeGainNormalizer()
    frame = _sine_int16(peak=0.008)
    out = norm.process(frame)
    assert _float_peak(out) < 0.05


def test_loud_frame_is_not_attenuated() -> None:
    # Amplify-only: a shout already triggers; the AGC must never turn it DOWN.
    norm = WakeGainNormalizer()
    frame = _sine_int16(peak=0.8)
    out = norm.process(frame)
    assert _float_peak(out) >= 0.79


def test_gain_is_capped_at_max_gain_db() -> None:
    # cap = 6 dB == 2x. A 0.05-peak frame must reach ~0.10, NOT the -3 dBFS
    # (0.707) target a >10x uncapped gain would produce.
    norm = WakeGainNormalizer(target_peak_dbfs=-3.0, max_gain_db=6.0)
    frame = _sine_int16(peak=0.05)
    out = norm.process(frame)
    out_peak = _float_peak(out)
    assert 0.05 * 2 * 0.9 <= out_peak <= 0.05 * 2 * 1.05


def test_reset_clears_the_envelope_state() -> None:
    norm = WakeGainNormalizer()
    norm.process(_sine_int16(peak=0.8))  # arm a high rolling peak
    norm.reset()
    # After reset a quiet frame is amplified as if it were the first one (the
    # stale loud envelope would otherwise suppress the gain).
    out = norm.process(_sine_int16(peak=0.06))
    assert _float_peak(out) > 0.06 * 1.5


# ---------------------------------------------------------------------------
# OpenWakeWordProvider integration
# ---------------------------------------------------------------------------

class _PeakSpyModel:
    """Records the float peak of every frame handed to ``predict`` and never
    fires, so we can assert what the model actually saw."""

    def __init__(self) -> None:
        self.peaks: list[float] = []

    def predict(self, frame: np.ndarray) -> dict[str, float]:
        self.peaks.append(_float_peak(np.asarray(frame)))
        return {"hey_jarvis": 0.0}


async def _one_frame(peak: float) -> AsyncIterator[AudioChunk]:
    pcm = _sine_int16(peak=peak).tobytes()
    yield AudioChunk(pcm=pcm, sample_rate=16_000, timestamp_ns=0)


async def _drain(provider: OpenWakeWordProvider, peak: float) -> _PeakSpyModel:
    spy = _PeakSpyModel()
    provider._model = spy  # noqa: SLF001 — inject the fake model (as test_wake_threshold does)
    async for _ in provider.detect(_one_frame(peak)):
        pass
    return spy


async def test_provider_amplifies_quiet_input_before_scoring() -> None:
    spy = await _drain(OpenWakeWordProvider(gain_normalization=True), peak=0.06)
    assert spy.peaks, "model should have been asked to score one frame"
    assert spy.peaks[0] > 0.06 * 1.5, (
        f"the model must see boosted audio, saw peak {spy.peaks[0]:.3f}"
    )


async def test_provider_gain_off_passes_raw_audio() -> None:
    spy = await _drain(OpenWakeWordProvider(gain_normalization=False), peak=0.06)
    assert spy.peaks
    assert abs(spy.peaks[0] - 0.06) < 0.01, (
        f"escape hatch must pass raw audio, saw peak {spy.peaks[0]:.3f}"
    )


async def test_gain_normalization_is_on_by_default() -> None:
    spy = await _drain(OpenWakeWordProvider(), peak=0.06)
    assert spy.peaks
    assert spy.peaks[0] > 0.06 * 1.5
