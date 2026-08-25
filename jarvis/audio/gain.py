"""Shared master-output-volume gain for EVERY TTS sink.

The user's ``[tts].volume`` is a single 0.0-1.0 knob. Raw TTS speech is far
quieter than mastered music (lots of dynamics + pauses -> a low average/RMS
level), so a plain 1:1 copy sounds weak next to a music track at the same system
volume. We therefore scale the knob up to a *makeup gain* -- 100% is a real
loudness boost, not just unity -- and hold the boosted signal under full scale
with a look-ahead peak limiter, so it gets louder WITHOUT distorting.
Attenuation (below the unity point) stays a plain multiply and can never clip.

Why a limiter and not a curve. Until 2026-08-25 the boost was bounded by a
static ``tanh`` transfer curve above a 0.6 knee. A fixed curve is a waveshaper:
it bends every half-wave it touches, and the bend IS harmonic distortion.
Measured on that curve at 100% volume: an input peak of 0.3 produced ~8% THD,
0.5 produced ~22%, and everything above 0.5 collapsed onto full scale -- audible
as a short rasp on the loud syllables of an otherwise clean voice (user report
2026-08-25). :class:`PeakLimiter` instead lowers the GAIN for the few
milliseconds around a peak and leaves the waveform's shape alone, so the same
loudness arrives without added harmonics.

One implementation, three call sites. The local float32 speaker path
(``AudioPlayer``), the browser-voice WebSocket int16 path, and the telephony
mu-law int16 path all route through these helpers, so loudness is identical on
every OS and every transport -- including a headless VPS with no local audio
device, where the voice is carried by the browser/telephony sinks. ``numpy`` is
a base dependency (``pyproject``: ``numpy>=1.26``), so this is safe on a slim
server too.

Tuning lives here and nowhere else: change :data:`_MAKEUP_GAIN` /
:data:`_LIMIT_CEILING` once and every sink follows.
"""
from __future__ import annotations

import time

import numpy as np

# 100% volume maps to this playback gain. Speech TTS typically sits well below
# mastered music in perceived loudness, so a generous makeup range with a
# limiter lets the user dial Jarvis up to (or past) music level from the slider
# alone -- no code change, no config editing. The unity (1:1, byte-identical)
# point is therefore at ``volume = 1 / _MAKEUP_GAIN`` (25% for 4.0). Turning the
# slider down from 100% walks smoothly from "loudest" through unity to silence.
_MAKEUP_GAIN = 4.0

# Peak the limiter aims for. Slightly under full scale so int16 rounding and the
# gain ramp's own overshoot at a buffer seam still land inside range.
_LIMIT_CEILING = 0.97

# How often the limiter recomputes its gain. One control block is also the
# look-ahead distance: the gain reaches its new value exactly when the block that
# needs it begins.
_CONTROL_MS = 1.0
# Gain reduction is held this long before it starts recovering, so a run of loud
# syllables is ridden at one steady level instead of pumping between them.
_HOLD_MS = 60.0
# Time to walk the gain back to unity once the hold expires. Long enough that the
# recovery hides under speech rather than breathing audibly in the pauses.
_RELEASE_MS = 250.0
# A gap longer than this means the sink went quiet (utterance finished, barge-in,
# a call between turns). The next buffer is a fresh start, so the gain state is
# dropped -- otherwise a sentence could open mid-duck and audibly swell.
_IDLE_RESET_S = 0.4
# Fallback rate for callers that do not name one. Every TTS sink in this repo
# runs at 24 kHz; the value only scales the limiter's time constants.
_DEFAULT_SAMPLE_RATE = 24_000


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
    """Playback gain for a 0.0-1.0 volume knob: ``0.0 ... _MAKEUP_GAIN``."""
    return clamp_volume(volume) * _MAKEUP_GAIN


class PeakLimiter:
    """Streaming look-ahead peak limiter with a per-sample gain ramp.

    Holds one gain value across calls, so a stream fed in successive buffers is
    limited as ONE continuous signal -- a gain that jumped at every buffer seam
    would itself be an audible click. Instances are cheap; give each sink
    (speaker, browser session, call) its own.

    How it works, per buffer:

    1. Split the samples into ``_CONTROL_MS`` control blocks and take each
       block's peak (channel-linked, so stereo never drifts apart).
    2. Turn each peak into the gain that would put it exactly on the ceiling.
    3. Look one block ahead -- a block's gain is the lower of its own and its
       successor's, so the gain is already down when the loud block starts.
    4. Ride that with a hold and a slow release, then interpolate the gain
       linearly across each block's samples.

    The result is a smooth gain CURVE multiplied onto the samples, so the
    waveform keeps its shape: loudness changes, harmonics do not appear. The
    control loop is sequential but runs ~1 iteration per millisecond of audio,
    which is nothing next to the playback it feeds.
    """

    __slots__ = ("_ceiling", "_gain", "_hold", "_last_call", "_primed")

    def __init__(self, ceiling: float = _LIMIT_CEILING) -> None:
        self._ceiling = float(ceiling)
        self._gain = 1.0
        self._hold = 0
        self._last_call = 0.0
        self._primed = False

    def reset(self) -> None:
        """Forget the gain state: the next buffer opens at unity."""
        self._gain = 1.0
        self._hold = 0
        self._last_call = 0.0
        self._primed = False

    def process(
        self, arr: np.ndarray, sample_rate: int = _DEFAULT_SAMPLE_RATE
    ) -> np.ndarray:
        """Limit float32 samples (1-D mono or 2-D ``N x channels``)."""
        if arr.size == 0:
            return arr
        now = time.monotonic()
        if self._last_call and now - self._last_call > _IDLE_RESET_S:
            self.reset()
        self._last_call = now

        rate = int(sample_rate) if sample_rate else _DEFAULT_SAMPLE_RATE
        hop = max(1, int(rate * _CONTROL_MS / 1000.0))
        frames = arr.shape[0]
        magnitude = np.abs(arr) if arr.ndim == 1 else np.abs(arr).max(axis=1)

        # Block peaks. The tail is zero-padded so the final partial block is
        # judged on the samples it actually has.
        blocks = -(-frames // hop)  # ceil
        padding = blocks * hop - frames
        if padding:
            magnitude = np.concatenate(
                [magnitude, np.zeros(padding, dtype=magnitude.dtype)]
            )
        peaks = magnitude.reshape(blocks, hop).max(axis=1).astype(np.float64)

        # The gain each block needs, then one block of look-ahead.
        needed = np.ones(blocks, dtype=np.float64)
        loud = peaks > self._ceiling
        if loud.any():
            needed[loud] = self._ceiling / peaks[loud]
        target = np.minimum(needed, np.concatenate([needed[1:], needed[-1:]]))

        # Ride it: instant duck (the look-ahead already paid for it), hold, then
        # a slow release. ``ramp_from`` is where the previous buffer left the
        # gain, which makes the curve continuous across the seam. The FIRST
        # buffer after a reset has no seam -- silence preceded it -- so it opens
        # already ducked instead of spending its first millisecond ramping down
        # over audio that is allowed to be loud from sample zero.
        ramp_from = self._gain if self._primed else min(self._gain, float(target[0]))
        self._primed = True
        hold_blocks = max(1, int(_HOLD_MS / _CONTROL_MS))
        release_step = (hop / rate) / (_RELEASE_MS / 1000.0)
        gains = np.empty(blocks, dtype=np.float64)
        gain = self._gain
        hold = self._hold
        for index in range(blocks):
            wanted = float(target[index])
            if wanted < gain:
                gain = wanted
                hold = hold_blocks
            elif hold > 0:
                hold -= 1
            elif gain < wanted:
                gain = min(wanted, gain + release_step)
            gains[index] = gain
        self._gain = gain
        self._hold = hold

        # Per-sample ramp: each block walks from the previous block's gain to its
        # own, so the curve is continuous everywhere.
        starts = np.concatenate([[ramp_from], gains[:-1]])
        step = np.arange(hop, dtype=np.float64) / hop
        curve = (
            np.repeat(starts, hop)
            + np.repeat(gains - starts, hop) * np.tile(step, blocks)
        )[:frames].astype(np.float32)

        out = arr * (curve if arr.ndim == 1 else curve[:, None])
        return out.astype(np.float32, copy=False)


def apply_output_gain(
    arr_f: np.ndarray,
    volume: float,
    *,
    sample_rate: int = _DEFAULT_SAMPLE_RATE,
    limiter: PeakLimiter | None = None,
) -> np.ndarray:
    """Apply the master volume to float32 samples in ``[-1, 1]`` (local path).

    - unity knob -> returns ``arr_f`` unchanged (no copy, byte-identical output),
    - attenuation (gain < 1) -> a plain multiply that can never clip,
    - boost (gain > 1) -> multiply, then the look-ahead peak limiter.

    Pass the sink's own ``limiter`` so successive buffers are limited as one
    continuous stream; without one, each call is limited in isolation, which is
    what a single self-contained buffer wants.
    """
    gain = effective_gain(volume)
    if gain == 1.0:
        return arr_f
    scaled = arr_f * gain
    if gain < 1.0:
        return scaled
    limiter = limiter if limiter is not None else PeakLimiter()
    limited = limiter.process(scaled, sample_rate)
    # The limiter holds the ceiling on its own; this only catches a sample that
    # slipped past its ramp (a peak in the first millisecond after a seam). One
    # cheap comparison, and the clip itself is skipped when nothing escaped.
    if limited.size and float(np.max(np.abs(limited))) > 1.0:
        limited = np.clip(limited, -1.0, 1.0)
    return limited


def apply_output_gain_pcm16(
    pcm: bytes,
    volume: float,
    *,
    sample_rate: int = _DEFAULT_SAMPLE_RATE,
    limiter: PeakLimiter | None = None,
) -> bytes:
    """Apply the master volume to int16 mono PCM bytes (browser + telephony).

    ``int16 -> float32 -> gain (+ limiter on boost) -> int16``, so a headless
    server's browser/telephony voice gets the SAME loudness as the local
    speaker. Empty input and a unity knob short-circuit to the input bytes, so
    the common path stays cheap.
    """
    gain = effective_gain(volume)
    if not pcm or gain == 1.0:
        return pcm
    arr = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    out = apply_output_gain(arr, volume, sample_rate=sample_rate, limiter=limiter)
    # Back to int16: the limiter keeps the signal under full scale, but clip as a
    # hard guard against a rounding/edge overshoot, then round-to-nearest.
    scaled = np.clip(out * 32768.0, -32768.0, 32767.0)
    return np.rint(scaled).astype(np.int16).tobytes()
