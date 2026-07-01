"""The OWW input AGC uses an ADAPTIVE floor + a real warm-up.

Two mission-2026-06-30 defects on the default openWakeWord path:

1. "Only triggers when I shout" — even after the 2026-06-28 gain fix, the AGC's
   noise floor was a FIXED 0.02. A genuinely quiet wake whose peak sat between
   the old floor (0.02) and true silence got ZERO amplification and under-scored
   the pinned 0.15 threshold. The floor must ADAPT to the mic's real ambient
   level so a quiet mic is no longer deaf.
2. Cold-start latency — ``start()`` loaded the ONNX model but ran no inference,
   so the FIRST real "Hey Jarvis" frame paid the onnxruntime graph-init cost.
   ``start()`` must prime the model with one throwaway inference.

The pinned 0.15 threshold and the amplify-only + sub-floor guards are unchanged,
so this lifts quiet wakes without widening the ambient false-fire band.
"""
from __future__ import annotations

import numpy as np

from jarvis.plugins.wake.openwakeword_provider import (
    OWW_FRAME_SAMPLES,
    OpenWakeWordProvider,
    WakeGainNormalizer,
)


def _sine_int16(peak: float, n: int = OWW_FRAME_SAMPLES) -> np.ndarray:
    t = np.arange(n, dtype=np.float32)
    wave = np.sin(2.0 * np.pi * 440.0 * t / 16_000.0) * float(peak)
    return (np.clip(wave, -1.0, 1.0) * 32767.0).astype(np.int16)


def _float_peak(frame: np.ndarray) -> float:
    return float(np.max(np.abs(frame.astype(np.float32) / 32768.0)))


# ---------------------------------------------------------------------------
# Adaptive floor — a quiet wake below the legacy 0.02 fixed floor is amplified.
# ---------------------------------------------------------------------------

def test_quiet_wake_below_legacy_fixed_floor_is_amplified() -> None:
    # peak 0.012 sat BELOW the old fixed 0.02 noise floor -> the legacy AGC left
    # it unchanged and it under-scored the 0.15 threshold ("only when shouting").
    # With the adaptive floor (start ~0.006) it is above the speech threshold and
    # IS amplified toward the target.
    norm = WakeGainNormalizer()
    out = norm.process(_sine_int16(peak=0.012))
    assert _float_peak(out) > 0.012 * 2.0, (
        f"a quiet wake must be lifted, got {_float_peak(out):.3f}"
    )


def test_adaptive_floor_settles_down_on_a_quiet_mic() -> None:
    # After sustained near-silent ambient the floor adapts DOWN, so an even
    # quieter wake (0.008) that a fresh normalizer would gate now gets amplified.
    norm = WakeGainNormalizer()
    for _ in range(60):
        norm.process(_sine_int16(peak=0.0015))
    out = norm.process(_sine_int16(peak=0.008))
    assert _float_peak(out) > 0.008 * 2.0


# ---------------------------------------------------------------------------
# Warm-up — start() primes the model so the first real frame is not cold.
# ---------------------------------------------------------------------------

class _WarmupSpyModel:
    """Records predict calls so we can prove start() ran a warm inference."""

    def __init__(self) -> None:
        self.predict_calls = 0

    def predict(self, _frame: object) -> dict[str, float]:
        self.predict_calls += 1
        return {"hey_jarvis": 0.0}


async def test_start_primes_the_model_with_a_warmup_inference() -> None:
    prov = OpenWakeWordProvider()
    spy = _WarmupSpyModel()
    prov._model = spy  # noqa: SLF001 — inject a fake so start() skips the real load
    await prov.start()
    assert spy.predict_calls >= 1, (
        "start() must run one throwaway inference so the first real wake frame "
        "does not pay the onnxruntime cold-start"
    )


async def test_start_warmup_never_raises_on_a_broken_model() -> None:
    class _BoomModel:
        def predict(self, _frame: object) -> dict[str, float]:
            raise RuntimeError("cold graph blew up")

    prov = OpenWakeWordProvider()
    prov._model = _BoomModel()  # noqa: SLF001
    # A warm-up failure must degrade to a no-op, never break voice boot.
    await prov.start()
