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


def generate_screen_capture_pcm(
    sample_rate: int = 24_000,
    amplitude: float = 0.42,
) -> bytes:
    """Generate an original mechanical-camera shutter cue.

    Two short deterministic noise transients form the shutter blades, with a
    quiet resonant body underneath. The sound is synthesized in memory so it
    is portable and carries no third-party recording or licensing baggage.
    """
    duration_s = 0.16
    n = int(duration_s * sample_rate)
    t = np.arange(n, dtype=np.float32) / float(sample_rate)
    rng = np.random.default_rng(0x5C4E_454E)
    noise = rng.uniform(-1.0, 1.0, n).astype(np.float32)

    wave = np.zeros(n, dtype=np.float32)
    for onset, strength, decay in ((0.0, 1.0, 150.0), (0.052, 0.78, 115.0)):
        local_t = np.maximum(t - onset, 0.0)
        active = (t >= onset).astype(np.float32)
        transient = noise * np.exp(-local_t * decay) * active
        resonance = np.sin(2.0 * np.pi * 1850.0 * local_t) * np.exp(
            -local_t * (decay * 0.62)
        ) * active
        wave += strength * (0.78 * transient + 0.22 * resonance)

    # A short fade avoids a digital edge while preserving the crisp first hit.
    fade_out = np.minimum((duration_s - t) * 180.0, 1.0)
    wave *= np.clip(fade_out, 0.0, 1.0) * amplitude
    return (np.clip(wave, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()


# Pre-generated at import time — loaded once by the pipeline
CHIME_PCM: bytes = generate_chime_pcm()
CHIME_SAMPLE_RATE: int = 24_000
DISCONNECT_PCM: bytes = generate_disconnect_pcm()
READY_PCM: bytes = generate_ready_pcm()
SCREEN_CAPTURE_PCM: bytes = generate_screen_capture_pcm()
