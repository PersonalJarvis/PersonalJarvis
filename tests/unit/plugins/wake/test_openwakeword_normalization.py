"""End-to-end: input normalization lets a quiet wake clear the threshold.

Companion to ``test_openwakeword_gain.py`` (which pins the ``WakeGainNormalizer``
mechanism in isolation). These tests close the loop on the user-visible symptom
("the wake word only triggers when I shout", mission 2026-06-28): they wire a
level-sensitive fake model — a genuine wake pattern is assumed present and the
score scales with the input frame's peak — so the normalization stage is what
decides whether the score actually clears the pinned 0.15 activation threshold.

Without normalization the quiet wake stays below threshold (the bug); with it
(the default), the same quiet "Hey Jarvis" wakes — without shouting.
"""
from __future__ import annotations

from collections.abc import AsyncIterator

import numpy as np

from jarvis.core.protocols import AudioChunk
from jarvis.plugins.wake.openwakeword_provider import (
    OWW_FRAME_SAMPLES,
    OpenWakeWordProvider,
)

_THRESHOLD = 0.15


class _LevelSensitiveModel:
    """Mimics openWakeWord's amplitude sensitivity: ``score = min(1, peak * k)``.

    Assumes the genuine wake pattern is present; only the LEVEL varies, so the
    provider's normalization stage decides whether the score clears threshold.
    """

    def __init__(self, gain_to_score: float) -> None:
        self._k = gain_to_score

    def predict(self, frame: object) -> dict[str, float]:
        arr = np.asarray(frame, dtype=np.float32) / 32768.0
        peak = float(np.max(np.abs(arr))) if arr.size else 0.0
        return {"hey_jarvis": min(1.0, peak * self._k)}


def _quiet_wake_frame(peak_amp: float) -> bytes:
    """One OWW frame of int16 audio at the given peak amplitude [0, 1]."""
    val = int(round(peak_amp * 32767.0))
    return np.full(OWW_FRAME_SAMPLES, val, dtype=np.int16).tobytes()


async def _chunks(frames: list[bytes]) -> AsyncIterator[AudioChunk]:
    for i, pcm in enumerate(frames):
        yield AudioChunk(pcm=pcm, sample_rate=16_000, timestamp_ns=i)


async def test_normalization_lets_a_quiet_wake_trigger() -> None:
    # peak 0.05 -> raw score 0.05 * 2 = 0.10 < 0.15 threshold. After amplify-only
    # normalization the frame is lifted to ~0.5, so the score clears the
    # threshold and the wake fires WITHOUT shouting.
    prov = OpenWakeWordProvider(activation_threshold=_THRESHOLD)  # gain on by default
    prov._model = _LevelSensitiveModel(gain_to_score=2.0)  # noqa: SLF001
    wakes = [kw async for kw in prov.detect(_chunks([_quiet_wake_frame(0.05)]))]
    assert wakes == ["hey_jarvis"]


async def test_without_normalization_the_quiet_wake_is_missed() -> None:
    # Documents the bug: with gain off the same quiet wake stays below threshold
    # — exactly the "only works when shouted" symptom.
    prov = OpenWakeWordProvider(activation_threshold=_THRESHOLD, gain_normalization=False)
    prov._model = _LevelSensitiveModel(gain_to_score=2.0)  # noqa: SLF001
    wakes = [kw async for kw in prov.detect(_chunks([_quiet_wake_frame(0.05)]))]
    assert wakes == []
