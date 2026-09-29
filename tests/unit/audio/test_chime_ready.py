"""The boot-ready cue: an audible "I'm listening now" after (cold) start.

After a reboot Jarvis needs ~25 s of warm-up before the mic opens. Saying
"Hey Jarvis" during that window does nothing, which the user reads as broken.
A short ascending tone played the moment warm-up finishes tells the user
exactly when Jarvis is ready — distinct from the wake chime so the two are
never confused.
"""
from __future__ import annotations

import numpy as np

from jarvis.audio.chime import (
    CHIME_PCM,
    CHIME_SAMPLE_RATE,
    DISCONNECT_PCM,
    READY_PCM,
    SCREEN_CAPTURE_PCM,
    SCREEN_CAPTURE_PREROLL_S,
    generate_ready_pcm,
    generate_screen_capture_pcm,
)


def test_ready_pcm_is_nonempty_int16() -> None:
    assert isinstance(READY_PCM, bytes)
    assert len(READY_PCM) > 0
    assert len(READY_PCM) % 2 == 0  # int16 samples -> even byte count


def test_ready_pcm_distinct_from_wake_and_disconnect() -> None:
    """The ready cue must be audibly its own signal, not a reused wake chime
    or hangup tone."""
    assert READY_PCM != CHIME_PCM
    assert READY_PCM != DISCONNECT_PCM


def test_generate_ready_pcm_is_deterministic() -> None:
    """Same parameters -> identical bytes (pre-generated once at import)."""
    assert generate_ready_pcm() == generate_ready_pcm()


def test_screen_capture_cue_is_original_and_deterministic() -> None:
    assert SCREEN_CAPTURE_PCM == generate_screen_capture_pcm()
    assert SCREEN_CAPTURE_PCM not in (CHIME_PCM, READY_PCM, DISCONNECT_PCM)
    assert len(SCREEN_CAPTURE_PCM) % 2 == 0


def test_screen_capture_cue_survives_a_fresh_stream_onset() -> None:
    """The cue must start after a silent pre-roll.

    ``play_pcm`` fades the first 5 ms and a freshly opened device swallows its
    start; a click that begins at sample 0 loses its whole transient there.
    """
    samples = np.frombuffer(SCREEN_CAPTURE_PCM, dtype=np.int16).astype(np.float64)
    preroll = int(SCREEN_CAPTURE_PREROLL_S * CHIME_SAMPLE_RATE)
    assert not samples[:preroll].any()
    assert np.abs(samples[preroll:]).max() > 0
    assert np.max(np.abs(samples)) < 32767 * 0.5  # soft, never clipping
