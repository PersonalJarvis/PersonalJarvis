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
    amplitude: float = 0.22,
) -> bytes:
    """Generate the appshot cue: a crisp, tactile "click-clack".

    Two short clicks, like a phone camera shutter. Each one is a damped high
    tick (a few kHz) over a whisper of band-limited noise, with a 1 ms attack
    so it snaps without a digital edge; the second is a touch lower and
    softer. Every onset sits after ``SCREEN_CAPTURE_PREROLL_S`` of silence,
    because a freshly opened stream fades and swallows its first milliseconds.
    Synthesized in memory: portable and free of third-party recordings.

    The peak stays low on purpose: the player's master volume adds up to 4x
    makeup gain, and a louder source would ride the peak limiter.
    """
    preroll = int(SCREEN_CAPTURE_PREROLL_S * sample_rate)
    body_s = 0.12
    n = int(body_s * sample_rate)
    t = np.arange(n, dtype=np.float64) / float(sample_rate)
    rng = np.random.default_rng(0x5C4E_454E)
    # Differentiated noise tilts the spectrum up: an airy tick, not a hiss.
    noise = np.diff(rng.uniform(-1.0, 1.0, n + 1))

    def _click(onset: float, pitch: float, strength: float) -> np.ndarray:
        local_t = np.maximum(t - onset, 0.0)
        active = t >= onset
        attack = np.clip(local_t / 0.001, 0.0, 1.0)
        tick = np.sin(2.0 * np.pi * pitch * local_t) * np.exp(-local_t * 260.0)
        body = np.sin(2.0 * np.pi * pitch * 0.5 * local_t) * np.exp(-local_t * 140.0)
        air = noise * np.exp(-local_t * 420.0)
        return strength * attack * (0.55 * tick + 0.25 * body + 0.35 * air) * active

    wave = _click(0.0, 3400.0, 1.0) + _click(0.048, 2600.0, 0.7)
    fade_out = np.clip((body_s - t) / 0.01, 0.0, 1.0)
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
