"""Regression guard for the wake-word activation threshold (BUG-009 episodes).

The OpenWakeWord threshold has drifted four times (0.40 -> 0.15 -> 0.10 ->
0.06), each time over- or under-correcting the previous value. Episode 5
(2026-05-24) was the 0.06 over-correction: the orb popped up on the entire
ambient speech band, firing on bare "Hallo" and room noise.

The score bands below are the empirically observed values from this project's
quiet-mic hardware (data/jarvis_desktop.log, 2026-05-24):

  * ambient speech / bare "Hallo" false-fires: 0.05 - 0.11
  * genuine "Hey Jarvis" utterance peaks:       0.15 - 0.23

These tests pin the production threshold so a future "make it more sensitive"
edit cannot silently drop OWW back into the ambient band.
"""
from __future__ import annotations

from collections.abc import AsyncIterator

from jarvis.core.protocols import AudioChunk
from jarvis.plugins.wake.openwakeword_provider import (
    OWW_FRAME_SAMPLES,
    PRODUCTION_WAKE_THRESHOLD,
    OpenWakeWordProvider,
)

# Observed false-fire scores that popped the orb from IDLE at threshold 0.06.
_AMBIENT_FALSE_FIRE_SCORES = (0.077, 0.069, 0.061, 0.091, 0.109, 0.113)
# Observed genuine "Hey Jarvis" burst peaks.
_GENUINE_WAKE_SCORES = (0.150, 0.163, 0.204, 0.232)


class _ScriptedModel:
    """Stands in for the openWakeWord ONNX model: returns one scripted score
    per ``predict`` call so we can drive ``detect`` deterministically."""

    def __init__(self, scores: tuple[float, ...]) -> None:
        self._scores = iter(scores)

    def predict(self, _frame: object) -> dict[str, float]:
        return {"hey_jarvis": next(self._scores, 0.0)}


def _silent_frame_bytes() -> bytes:
    # Exactly one OWW frame of int16 silence -> one predict() call per chunk.
    return b"\x00\x00" * OWW_FRAME_SAMPLES


async def _one_chunk_per_score(n: int) -> AsyncIterator[AudioChunk]:
    pcm = _silent_frame_bytes()
    for i in range(n):
        yield AudioChunk(pcm=pcm, sample_rate=16_000, timestamp_ns=i)


async def _collect_wakes(scores: tuple[float, ...]) -> list[str]:
    prov = OpenWakeWordProvider(activation_threshold=PRODUCTION_WAKE_THRESHOLD)
    prov._model = _ScriptedModel(scores)  # noqa: SLF001 — inject the fake model
    return [kw async for kw in prov.detect(_one_chunk_per_score(len(scores)))]


def test_production_threshold_stays_above_ambient_band() -> None:
    # The hard floor. Below 0.10 the ambient band leaks through (the BUG-009
    # episode-5 over-correction). The upper bound keeps the quiet mic usable.
    assert 0.10 <= PRODUCTION_WAKE_THRESHOLD <= 0.30


async def test_ambient_scores_do_not_trigger_a_wake() -> None:
    wakes = await _collect_wakes(_AMBIENT_FALSE_FIRE_SCORES)
    assert wakes == [], (
        f"ambient speech band {_AMBIENT_FALSE_FIRE_SCORES} must not wake at "
        f"threshold {PRODUCTION_WAKE_THRESHOLD}"
    )


async def test_genuine_hey_jarvis_peaks_still_trigger() -> None:
    # Each genuine peak alone (cooldown lets only the first through, which is
    # all we need to prove the band is accepted).
    for score in _GENUINE_WAKE_SCORES:
        wakes = await _collect_wakes((score,))
        assert wakes == ["hey_jarvis"], (
            f"genuine wake score {score} must trigger at threshold "
            f"{PRODUCTION_WAKE_THRESHOLD}"
        )
