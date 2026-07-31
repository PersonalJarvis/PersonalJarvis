"""Deterministic fixed-buffer budget for the classic voice path."""
from __future__ import annotations

from jarvis.audio.capture import BLOCKSIZE, SAMPLE_RATE
from jarvis.audio.player import TTS_FIRST_WRITE_BUFFER_MS

_LEGACY_CAPTURE_BLOCK_MS = 100.0
_LEGACY_FIRST_WRITE_MS = 120.0


def test_fixed_chunking_budget_is_below_one_vad_frame_plus_first_playout() -> None:
    capture_ms = BLOCKSIZE / SAMPLE_RATE * 1000.0
    fixed_budget_ms = capture_ms + TTS_FIRST_WRITE_BUFFER_MS
    legacy_budget_ms = _LEGACY_CAPTURE_BLOCK_MS + _LEGACY_FIRST_WRITE_MS

    assert fixed_budget_ms <= 75.0
    assert legacy_budget_ms - fixed_budget_ms >= 140.0
    assert fixed_budget_ms / legacy_budget_ms < 0.35
