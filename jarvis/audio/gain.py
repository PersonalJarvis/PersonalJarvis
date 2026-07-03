"""Shared master-output-volume gain for EVERY TTS sink.

The user's ``[tts].volume`` is a single 0.0–1.0 knob. Raw TTS speech is far
quieter than mastered music (lots of dynamics + pauses → a low average/RMS
level), so a plain 1:1 copy sounds weak next to a music track at the same system
volume. We therefore scale the knob up to a *makeup gain* — 100% is a real
loudness boost, not just unity — and run the boosted signal through a soft-knee
limiter so it gets louder WITHOUT hard-clipping into distortion. Attenuation
(below the unity point) stays a plain multiply and can never clip.

One implementation, three call sites. The local float32 speaker path
(``AudioPlayer``), the browser-voice WebSocket int16 path, and the telephony
mu-law int16 path all route through these helpers, so loudness is identical on
every OS and every transport — including a headless VPS with no local audio
device, where the voice is carried by the browser/telephony sinks. ``numpy`` is
a base dependency (``pyproject``: ``numpy>=1.26``), so this is safe on a slim
server too.

Tuning lives here and nowhere else: change :data:`_MAKEUP_GAIN` /
:data:`_LIMIT_KNEE` once and every sink follows.
"""
from __future__ import annotations

import numpy as np

# 100% volume maps to this playback gain. Speech TTS typically sits well below
# mastered music in perceived loudness, so a generous makeup range with a soft
# limiter lets the user dial Jarvis up to (or past) music level from the slider
# alone — no code change, no config editing. The unity (1:1, byte-identical)
# point is therefore at ``volume = 1 / _MAKEUP_GAIN`` (25% for 4.0). Turning the
# slider down from 100% walks smoothly from "loudest" through unity to silence.
_MAKEUP_GAIN = 4.0

# Soft-knee limiter threshold. Samples whose magnitude is below this pass through
# UNTOUCHED (transparent) — that is the bulk of quiet speech, which is exactly
# what the boost is meant to lift. Only the range ABOVE the knee is compressed
# toward full-scale, so real peaks saturate gently instead of clipping. A global
# ``tanh`` (no knee) would soften the whole signal and dull the voice; the knee
# keeps quiet detail linear and shapes only what would otherwise clip.
_LIMIT_KNEE = 0.6


def clamp_volume(volume: float) -> float:
    """Clamp the user volume knob into ``[0.0, 1.0]``.

    A value outside the range is pinned to the bounds; a non-numeric value falls
    back to full volume (1.0) rather than muting, so a corrupt config can never
    silence Jarvis.
    """
    try:
        return max(0.0, min(1.0, float(volume)))
    except (TypeError, ValueError):
        return 1.0


def effective_gain(volume: float) -> float:
    """Playback gain for a 0.0–1.0 volume knob: ``0.0 … _MAKEUP_GAIN``."""
    return clamp_volume(volume) * _MAKEUP_GAIN


def soft_limit(arr: np.ndarray) -> np.ndarray:
    """Soft-knee limiter on float32 samples.

    Below ``±_LIMIT_KNEE`` the signal is untouched (transparent). Above the knee
    the excess magnitude is compressed with ``tanh`` so the output asymptotes to
    ``±1.0`` — boosted peaks saturate gently instead of clipping. Returns a new
    array; the input is not mutated.
    """
    out = arr.astype(np.float32, copy=True)
    over = np.abs(arr) > _LIMIT_KNEE
    if over.any():
        sign = np.sign(arr[over])
        excess = np.abs(arr[over]) - _LIMIT_KNEE
        head = 1.0 - _LIMIT_KNEE
        out[over] = sign * (_LIMIT_KNEE + head * np.tanh(excess / head))
    return out


def apply_output_gain(arr_f: np.ndarray, volume: float) -> np.ndarray:
    """Apply the master volume to float32 samples in ``[-1, 1]`` (local path).

    - unity knob → returns ``arr_f`` unchanged (no copy, byte-identical output),
    - attenuation (gain < 1) → a plain multiply that can never clip,
    - boost (gain > 1) → multiply then the soft-knee limiter.
    """
    gain = effective_gain(volume)
    if gain == 1.0:
        return arr_f
    scaled = arr_f * gain
    return soft_limit(scaled) if gain > 1.0 else scaled


def apply_output_gain_pcm16(pcm: bytes, volume: float) -> bytes:
    """Apply the master volume to int16 mono PCM bytes (browser + telephony).

    ``int16 → float32 → gain (+ soft limiter on boost) → int16``, so a headless
    server's browser/telephony voice gets the SAME loudness as the local
    speaker. Empty input and a unity knob short-circuit to the input bytes, so
    the common path stays cheap.
    """
    gain = effective_gain(volume)
    if not pcm or gain == 1.0:
        return pcm
    arr = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    out = apply_output_gain(arr, volume)
    # Back to int16: the soft limiter keeps |out| < 1, but clip as a hard guard
    # against a rounding/edge overshoot, then round-to-nearest.
    scaled = np.clip(out * 32768.0, -32768.0, 32767.0)
    return np.rint(scaled).astype(np.int16).tobytes()
