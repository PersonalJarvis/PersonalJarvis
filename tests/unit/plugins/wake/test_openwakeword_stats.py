"""Debug counters + suppression-reason visibility for the OWW wake path.

Mission 2026-06-29 ("wake never triggers / sometimes stops entirely"): the
fast OpenWakeWord detector used to ``continue`` silently whenever a frame was
below threshold or inside the cooldown, so a user testing the wake word had no
way to see *why* nothing happened — was the score close? was a stale cooldown
deafening the detector? The provider now keeps per-session counters and exposes
a ``stats()`` snapshot, and ``detect()`` resets its per-session audio state on
entry so a stale-loud gain envelope from the previous interaction can never
under-amplify (and thus block) the next quiet wake (the "no dead state blocks
waking" requirement).

These pin:
* every scored frame is counted;
* a below-threshold frame is counted as a below-threshold suppression;
* an above-threshold frame that the cooldown swallows is counted separately
  (so "I said it twice and only the first worked" is explainable, not silent);
* the loudest score this session is tracked (the "confidence scores" log);
* ``detect()`` resets the gain envelope on entry (the dead-state guard).
"""
from __future__ import annotations

from collections.abc import AsyncIterator

import numpy as np
import pytest

from jarvis.core.protocols import AudioChunk
from jarvis.plugins.wake.openwakeword_provider import (
    OWW_FRAME_SAMPLES,
    PRODUCTION_WAKE_THRESHOLD,
    OpenWakeWordProvider,
)


class _ScriptedModel:
    """Returns one scripted score per ``predict`` call (like test_wake_threshold)."""

    def __init__(self, scores: tuple[float, ...]) -> None:
        self._scores = iter(scores)

    def predict(self, _frame: object) -> dict[str, float]:
        return {"hey_jarvis": next(self._scores, 0.0)}


def _silent_frame_bytes() -> bytes:
    return b"\x00\x00" * OWW_FRAME_SAMPLES


async def _one_chunk_per_score(n: int) -> AsyncIterator[AudioChunk]:
    pcm = _silent_frame_bytes()
    for i in range(n):
        yield AudioChunk(pcm=pcm, sample_rate=16_000, timestamp_ns=i)


async def _drain(prov: OpenWakeWordProvider, scores: tuple[float, ...]) -> list[str]:
    prov._model = _ScriptedModel(scores)  # noqa: SLF001 — inject the fake model
    return [kw async for kw in prov.detect(_one_chunk_per_score(len(scores)))]


# ---------------------------------------------------------------------------
# Counters
# ---------------------------------------------------------------------------

async def test_stats_count_frames_below_threshold_and_one_trigger() -> None:
    prov = OpenWakeWordProvider(activation_threshold=0.15)
    wakes = await _drain(prov, (0.05, 0.09, 0.20))
    s = prov.stats()
    assert wakes == ["hey_jarvis"]
    assert s["frames_seen"] == 3
    assert s["triggers"] == 1
    assert s["suppressed_below_threshold"] == 2
    assert s["max_score"] == pytest.approx(0.20)


async def test_stats_count_cooldown_suppression_separately() -> None:
    # Two genuine scores back-to-back: the first fires, the cooldown swallows
    # the second. That second swallow must be visible as a cooldown suppression,
    # NOT a below-threshold one (it cleared the bar — the cooldown ate it).
    prov = OpenWakeWordProvider(activation_threshold=0.15, cooldown_s=2.0)
    wakes = await _drain(prov, (0.20, 0.21))
    s = prov.stats()
    assert wakes == ["hey_jarvis"]
    assert s["triggers"] == 1
    assert s["attempts_above_threshold"] == 2
    assert s["suppressed_cooldown"] == 1
    assert s["suppressed_below_threshold"] == 0


async def test_stats_start_at_zero_before_any_audio() -> None:
    prov = OpenWakeWordProvider()
    s = prov.stats()
    assert s["frames_seen"] == 0
    assert s["triggers"] == 0
    assert s["suppressed_below_threshold"] == 0
    assert s["suppressed_cooldown"] == 0


# ---------------------------------------------------------------------------
# Per-session state reset — the "no dead state blocks waking" guard
# ---------------------------------------------------------------------------

def _sine_int16(peak: float, n: int = OWW_FRAME_SAMPLES) -> np.ndarray:
    t = np.arange(n, dtype=np.float32)
    wave = np.sin(2.0 * np.pi * 440.0 * t / 16_000.0) * float(peak)
    return (np.clip(wave, -1.0, 1.0) * 32767.0).astype(np.int16)


def _float_peak(frame: np.ndarray) -> float:
    return float(np.max(np.abs(np.asarray(frame).astype(np.float32) / 32768.0)))


class _PeakSpyModel:
    def __init__(self) -> None:
        self.peaks: list[float] = []

    def predict(self, frame: np.ndarray) -> dict[str, float]:
        self.peaks.append(_float_peak(frame))
        return {"hey_jarvis": 0.0}


async def _one_frame(peak: float) -> AsyncIterator[AudioChunk]:
    yield AudioChunk(pcm=_sine_int16(peak=peak).tobytes(), sample_rate=16_000, timestamp_ns=0)


async def test_detect_resets_stale_loud_gain_envelope_on_entry() -> None:
    # A loud burst at the END of the previous interaction (e.g. Jarvis's own
    # TTS bleeding into the mic, or a door slam) arms the rolling-peak envelope.
    # If that envelope survived into the next listen, a normal-volume "Hey
    # Jarvis" would be left UN-amplified for ~1.5 s and under-score the
    # threshold — the wake "randomly stops working". detect() must reset the
    # envelope on entry so the first quiet frame is amplified as if it were the
    # first one ever.
    prov = OpenWakeWordProvider(gain_normalization=True)
    prov._gain.process(_sine_int16(peak=0.9))  # noqa: SLF001 — arm a loud peak
    spy = _PeakSpyModel()
    prov._model = spy  # noqa: SLF001
    async for _ in prov.detect(_one_frame(0.06)):
        pass
    assert spy.peaks, "model should have scored one frame"
    assert spy.peaks[0] > 0.06 * 1.5, (
        f"stale loud envelope blocked the quiet-wake boost: saw {spy.peaks[0]:.3f}"
    )


# ---------------------------------------------------------------------------
# Threshold consistency — omitting the threshold == the documented production
# value, not a quieter ad-hoc default.
# ---------------------------------------------------------------------------

def test_default_threshold_is_the_documented_production_value() -> None:
    prov = OpenWakeWordProvider()
    assert prov._threshold == pytest.approx(PRODUCTION_WAKE_THRESHOLD)  # noqa: SLF001
