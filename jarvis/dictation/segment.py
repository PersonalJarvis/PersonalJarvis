"""Where to cut a dictation recording into transcribable segments.

Why segmenting matters at all: transcribing the whole growing buffer on every
tick costs O(n²) in audio seconds and, on a metered API, real money — a
two-minute dictation re-sends the entire recording roughly a hundred times.
Closing segments as they finish turns that into one pass over the audio.

Why cutting is not trivial: a cut in the middle of a word produces two wrong
transcriptions instead of one right one, and the damage is permanent because a
closed segment is never re-transcribed. So the cut point is not "exactly at N
seconds" but "at the quietest moment near N seconds" — which, in dictated
speech, is a pause between words far more often than not.

Pure functions over raw PCM: no model, no VAD state machine, no I/O. The VAD
proper is deliberately not reused here — it is a stateful component on the
voice hot path, and dictation must never contend with it.
"""

from __future__ import annotations

import numpy as np

#: Samples are 16-bit little-endian mono.
BYTES_PER_SAMPLE = 2

#: How far back from the nominal cut we are willing to look for a quiet spot.
#: Wide enough to reach the previous pause in normal speech, narrow enough that
#: segments stay roughly the requested length.
_SEARCH_BACK_S = 1.5

#: Width of the loudness window scored while searching. Roughly the length of a
#: natural inter-word pause.
_PROBE_WINDOW_S = 0.2

#: Step between scored positions. Finer than this buys no accuracy for the cost.
_PROBE_STEP_S = 0.02


def _align(offset: int) -> int:
    """Round an offset down to a whole int16 sample boundary."""
    return max(0, offset - (offset % BYTES_PER_SAMPLE))


def quietest_cut(
    pcm: bytes,
    nominal_bytes: int,
    bytes_per_second: int = 16_000 * BYTES_PER_SAMPLE,
) -> int:
    """Byte offset at which to close a segment of ``pcm``.

    Looks in ``[nominal - 1.5 s, nominal]`` for the quietest short window and
    returns its centre; falls back to ``nominal_bytes`` when the buffer is too
    short to search or anything about the scan goes wrong. The result is always
    sample-aligned and never past the end of ``pcm``.
    """
    if nominal_bytes <= 0:
        return 0
    limit = min(len(pcm), nominal_bytes)
    if limit <= 0:
        return 0

    window = int(_PROBE_WINDOW_S * bytes_per_second)
    step = max(BYTES_PER_SAMPLE, int(_PROBE_STEP_S * bytes_per_second))
    search_start = max(0, limit - int(_SEARCH_BACK_S * bytes_per_second))
    if window <= 0 or limit - search_start < window * 2:
        # Too little audio to make a meaningful choice — the nominal cut is as
        # good as any, and pretending otherwise would just add jitter.
        return _align(limit)

    try:
        samples = np.frombuffer(pcm[:limit], dtype=np.int16).astype(np.float32)
    except (ValueError, TypeError):
        return _align(limit)
    if samples.size == 0:
        return _align(limit)

    window_samples = max(1, window // BYTES_PER_SAMPLE)
    step_samples = max(1, step // BYTES_PER_SAMPLE)
    first = search_start // BYTES_PER_SAMPLE

    best_energy: float | None = None
    best_centre = samples.size
    for start in range(first, samples.size - window_samples + 1, step_samples):
        chunk = samples[start : start + window_samples]
        energy = float(np.mean(np.abs(chunk)))
        if best_energy is None or energy < best_energy:
            best_energy = energy
            best_centre = start + window_samples // 2

    if best_energy is None:
        return _align(limit)
    return _align(min(limit, best_centre * BYTES_PER_SAMPLE))


__all__ = ["BYTES_PER_SAMPLE", "quietest_cut"]
