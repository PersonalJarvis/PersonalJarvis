"""Wave-1 latency fix: the pipeline playback stall watchdog.

A device wedge used to be caught only by a flat 120 s ceiling. The watchdog
now reads the player's ``last_write_ns`` progress and trips in ~5 s when audio
frames stop reaching PortAudio mid-playback — but never during the legitimate
pre-first-frame window (owned by the brain stall guard).
"""
from __future__ import annotations

import time

from jarvis.speech.pipeline import _playback_progress_stalled


def test_progress_stalled_true_when_no_writes_for_window() -> None:
    last_write_ns = time.monotonic_ns() - int(6e9)  # last frame 6 s ago
    assert _playback_progress_stalled(last_write_ns, stall_s=5.0) is True


def test_progress_not_stalled_when_recent_write() -> None:
    last_write_ns = time.monotonic_ns() - int(1e9)  # last frame 1 s ago
    assert _playback_progress_stalled(last_write_ns, stall_s=5.0) is False


def test_progress_not_stalled_before_first_frame() -> None:
    # last_write_ns == 0 → playback hasn't produced its first frame yet. That
    # window belongs to the brain/producer stall guard, NOT this watchdog,
    # otherwise a slow first token would be misread as a device wedge.
    assert _playback_progress_stalled(0, stall_s=5.0) is False
