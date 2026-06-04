"""The boot-ready cue: an audible "I'm listening now" after (cold) start.

After a reboot Jarvis needs ~25 s of warm-up before the mic opens. Saying
"Hey Jarvis" during that window does nothing, which the user reads as broken.
A short ascending tone played the moment warm-up finishes tells the user
exactly when Jarvis is ready — distinct from the wake chime so the two are
never confused.
"""
from __future__ import annotations

from jarvis.audio.chime import (
    CHIME_PCM,
    DISCONNECT_PCM,
    READY_PCM,
    generate_ready_pcm,
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
