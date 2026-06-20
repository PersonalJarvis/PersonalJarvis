"""Stdlib-only codec/endpoint helpers for the browser-voice bridge.

This is a thin RE-EXPORT of the telephony bridge's audio layer — the same
``audioop``-based ``Resampler`` + the torch-free ``EnergyEndpointer`` — so the
browser path inherits any fix there for free and the dependency graph stays
readable. NEVER imports sounddevice / pyaudio: the browser owns the mic/speaker.
"""
from __future__ import annotations

from jarvis.telephony.audio import (
    STT_SAMPLE_RATE,
    TTS_SAMPLE_RATE,
    EnergyEndpointer,
    Resampler,
    resample_pcm16,
)

__all__ = [
    "EnergyEndpointer",
    "Resampler",
    "resample_pcm16",
    "STT_SAMPLE_RATE",
    "TTS_SAMPLE_RATE",
]
