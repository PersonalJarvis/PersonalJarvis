"""In-memory chime generator — a short sine tone used as an acknowledgment.

No WAV file needed; generated once at import time and cached as int16 PCM
bytes. Play back via `AudioPlayer.play_pcm(chime_bytes, 24000)`.

Design:
- Two overlaid sine tones (880 Hz + 1320 Hz) for a "ding" quality.
- Exponential decay so it does not sound like a rectangular pulse (no click).
- Short (~180 ms) — below the perceived latency threshold.
"""
from __future__ import annotations

import numpy as np


def generate_chime_pcm(
    duration_s: float = 0.18,
    sample_rate: int = 24_000,
    frequencies: tuple[float, ...] = (880.0, 1320.0),
    amplitude: float = 0.35,
) -> bytes:
    """Generate a short ding sound as int16 PCM bytes."""
    n = int(duration_s * sample_rate)
    t = np.linspace(0, duration_s, n, endpoint=False, dtype=np.float32)
    wave = np.zeros(n, dtype=np.float32)
    for f in frequencies:
        wave += np.sin(2.0 * np.pi * f * t)
    wave /= len(frequencies)
    # Exponential decay + short fade-in to avoid clicks
    decay = np.exp(-t * 18.0)
    fade_in = np.minimum(t * 100.0, 1.0)
    wave *= decay * fade_in * amplitude
    return (np.clip(wave, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()


def generate_disconnect_pcm(
    sample_rate: int = 24_000,
    amplitude: float = 0.35,
) -> bytes:
    """Descending 2-tone call-end signal (like a telephone 'disconnected').

    Two short beeps: first 660 Hz, then 440 Hz — subtle but clearly perceived
    as 'down / finished'.
    """
    def _beep(freq: float, duration_s: float) -> np.ndarray:
        n = int(duration_s * sample_rate)
        t = np.linspace(0, duration_s, n, endpoint=False, dtype=np.float32)
        wave = np.sin(2.0 * np.pi * freq * t)
        # Fade-in 10 ms, fade-out exponential
        fade_in = np.minimum(t * 100.0, 1.0)
        decay = np.exp(-t * 14.0)
        return (wave * fade_in * decay * amplitude).astype(np.float32)

    beep1 = _beep(660.0, 0.10)  # 100 ms
    gap = np.zeros(int(0.03 * sample_rate), dtype=np.float32)  # 30 ms pause
    beep2 = _beep(440.0, 0.14)  # 140 ms
    wave = np.concatenate([beep1, gap, beep2])
    return (np.clip(wave, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()


def generate_ready_pcm(
    sample_rate: int = 24_000,
    amplitude: float = 0.32,
) -> bytes:
    """Ascending 3-tone "ready / online" cue played once when warm-up finishes.

    Rises C5 -> E5 -> G5 (a major arpeggio) — deliberately the *opposite*
    direction of ``generate_disconnect_pcm`` so the user reads it as
    "powered up / listening now", and distinct from the single-ding wake
    chime so the two are never confused. ~330 ms total.
    """
    def _beep(freq: float, duration_s: float) -> np.ndarray:
        n = int(duration_s * sample_rate)
        t = np.linspace(0, duration_s, n, endpoint=False, dtype=np.float32)
        wave = np.sin(2.0 * np.pi * freq * t)
        fade_in = np.minimum(t * 100.0, 1.0)
        decay = np.exp(-t * 9.0)
        return (wave * fade_in * decay * amplitude).astype(np.float32)

    beep1 = _beep(523.25, 0.10)  # C5
    gap = np.zeros(int(0.015 * sample_rate), dtype=np.float32)  # 15 ms
    beep2 = _beep(659.25, 0.10)  # E5
    beep3 = _beep(783.99, 0.13)  # G5
    wave = np.concatenate([beep1, gap, beep2, gap, beep3])
    return (np.clip(wave, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()


#: Silence ahead of the appshot cue. ``play_pcm`` opens a fresh output stream
#: per cue, and its 5 ms edge fade plus the device's start-up swallow the first
#: few milliseconds; a cue that starts at sample 0 loses its onset there.
SCREEN_CAPTURE_PREROLL_S: float = 0.045


def generate_screen_capture_pcm(
    sample_rate: int = 24_000,
    amplitude: float = 0.2,
) -> bytes:
    """Generate the appshot cue: a soft, rounded two-note "bloop".

    A sine that glides up a fourth (D5 -> G5) lands on a gentle D6 bell, both
    with a smooth attack and a short synthetic room tail. Every onset sits
    after ``SCREEN_CAPTURE_PREROLL_S`` of silence, so a freshly opened stream
    plays the whole cue (the former noise-burst shutter packed half its
    energy into the first 5 ms and was inaudible). Synthesized in memory:
    portable and free of third-party recordings.

    The peak stays low on purpose: the player's master volume adds up to 4x
    makeup gain, and a louder source would ride the peak limiter instead of
    staying soft.
    """
    preroll = int(SCREEN_CAPTURE_PREROLL_S * sample_rate)
    body_s = 0.34
    n = int(body_s * sample_rate)
    t = np.arange(n, dtype=np.float64) / float(sample_rate)

    def _note(
        onset: float,
        f_start: float,
        f_end: float,
        glide_s: float,
        attack_s: float,
        decay: float,
    ) -> np.ndarray:
        local_t = np.maximum(t - onset, 0.0)
        active = t >= onset
        # Smoothstep glide, integrated into phase so the pitch bend has no seam.
        g = np.clip(local_t / glide_s, 0.0, 1.0) if glide_s > 0 else np.ones_like(t)
        freq = f_start + (f_end - f_start) * (g * g * (3.0 - 2.0 * g))
        phase = 2.0 * np.pi * np.cumsum(freq * active) / sample_rate
        tone = np.sin(phase) + 0.12 * np.sin(2.0 * phase)  # a touch of warmth
        attack = 0.5 - 0.5 * np.cos(np.pi * np.clip(local_t / attack_s, 0.0, 1.0))
        return tone * attack * np.exp(-local_t * decay) * active

    dry = _note(0.0, 587.33, 783.99, 0.05, 0.008, 20.0)
    dry += 0.8 * _note(0.075, 1174.66, 1174.66, 0.0, 0.010, 13.0)

    # A few soft, darkened early reflections give the cue air without a
    # convolution dependency.
    wave = dry.copy()
    for delay_s, gain in ((0.031, 0.22), (0.053, 0.15), (0.083, 0.09)):
        shift = int(delay_s * sample_rate)
        echo = np.zeros_like(dry)
        echo[shift:] = dry[:-shift]
        echo = np.convolve(echo, np.ones(6) / 6.0, mode="same")
        wave += gain * echo

    fade_out = np.clip((body_s - t) / 0.04, 0.0, 1.0)
    wave *= fade_out
    wave *= amplitude / max(float(np.max(np.abs(wave))), 1e-9)
    wave = np.concatenate([np.zeros(preroll), wave])
    return (np.clip(wave, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()


# Pre-generated at import time — loaded once by the pipeline
CHIME_PCM: bytes = generate_chime_pcm()
CHIME_SAMPLE_RATE: int = 24_000
DISCONNECT_PCM: bytes = generate_disconnect_pcm()
READY_PCM: bytes = generate_ready_pcm()
SCREEN_CAPTURE_PCM: bytes = generate_screen_capture_pcm()
