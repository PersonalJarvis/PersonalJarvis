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

A second job lives here: deciding that a stretch of audio holds no speech at
all, so it is never uploaded. Silence is not free — every silent second costs a
transcription request, and a request over silence is the single most reliable
way to make Whisper invent a sentence ("Thank you for watching", subtitle
credits, and friends). A user who pauses to think therefore paid twice: once in
rate limit, once in fabricated words landing in their text.

Judged on ENERGY only, never on what came back as text. That is the same rule
the wake path learned the hard way (AP-27): a content test cannot separate a
hallucination from a real utterance, because both are just words.

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


#: Shortest window the final pass will emit on its own. A sliver of audio is
#: the case a recognizer answers with a hallucinated subtitle credit, and it
#: costs a whole request to be told so.
_MIN_WINDOW_S = 1.0


def quality_windows(
    pcm: bytes,
    *,
    window_bytes: int,
    overlap_bytes: int = 0,
    bytes_per_second: int = 16_000 * BYTES_PER_SAMPLE,
) -> list[tuple[int, int]]:
    """Byte ranges covering ALL of ``pcm``, overlapping and cut at pauses.

    The shape the FINAL transcription uses, and it differs from the live
    segmentation above in both of its properties:

    * **Long windows.** A recognizer detects the spoken language from the audio
      it is handed. Four seconds is not enough to be sure, and an unsure model
      does not merely mislabel — it TRANSLATES. Twenty to thirty seconds is
      long enough that detection is reliable and short enough to stay inside
      every provider's upload limit.
    * **They overlap.** Consecutive windows share ``overlap_bytes`` of audio,
      so a word straddling a boundary is spoken in full inside at least one of
      them. The duplicate that buys is removed from the TEXT afterwards
      (:func:`jarvis.dictation.merge.merge_transcripts`) — which is possible,
      while recovering a word that was cut in half is not.

    Each window still ends at the quietest point near its nominal length, so
    the seam usually lands in a pause and the overlap has little to repair.

    Always makes progress: a pathological ``overlap_bytes`` (larger than the
    window, negative, absurd) cannot produce a window that starts where the
    previous one did. Returns ``[]`` for empty input.
    """
    total = len(pcm)
    if total <= 0 or window_bytes <= 0:
        return []
    window = _align(window_bytes)
    # More than half a window of overlap can never improve a seam enough to
    # justify re-reading most of the same audio. More importantly, clamping to
    # ``window - one sample`` technically made progress but could turn a bad
    # 5s/5s configuration into millions of two-byte windows. Half a window is
    # still far above the recommended 1--2 seconds and gives a hard O(n) bound.
    overlap = max(0, min(_align(overlap_bytes), window // 2))
    min_window = int(_MIN_WINDOW_S * bytes_per_second)

    ranges: list[tuple[int, int]] = []
    start = 0
    while start < total:
        remaining = total - start
        if remaining <= window:
            ranges.append((start, total))
            break
        cut = quietest_cut(pcm[start:], window, bytes_per_second)
        if cut < min_window:
            # The scan found its quiet spot too early to be a useful window —
            # take the nominal length instead. The overlap is what protects the
            # word this cut may land inside.
            cut = window
        end = _align(min(total, start + cut))
        if end <= start:  # pragma: no cover — _align keeps this unreachable
            end = min(total, start + window)
        ranges.append((start, end))
        # Step back by the overlap, but never to (or behind) this window's own
        # start: that would transcribe the same audio forever.
        # A pause search may cut as early as 60% of the nominal window. Keep a
        # half-window minimum stride even then: otherwise a legal 50% overlap
        # plus the earliest cut advances by only 10% and multiplies requests.
        start = max(end - overlap, start + window // 2)
    return ranges


#: Below these an int16 microphone signal cannot be carrying speech on ANY
#: gain setting worth calling working — this is room tone, a fan, a desk bump.
#: ~1.2 % of full scale peak. Deliberately far under the quietest usable speech
#: (which peaks in the thousands) because the cost of the two mistakes is not
#: symmetric: skipping real speech deletes it permanently, while transcribing a
#: little silence only costs one request.
_CERTAIN_SILENCE_PEAK = 400.0
_CERTAIN_SILENCE_RMS = 120.0

#: The relative test only unlocks once the session has actually heard something
#: loud enough to BE speech. Without this floor a whole dictation recorded at a
#: very low input gain would measure its own quiet speech against itself and
#: skip all of it.
_SPEECH_REFERENCE_PEAK = 3_000.0

#: How far under the session's loudest moment a stretch has to sit before it
#: counts as a pause rather than quiet speech.
_RELATIVE_SILENCE_FRACTION = 0.06
_RELATIVE_SILENCE_RMS = 250.0


def segment_energy(pcm: bytes) -> tuple[float, float]:
    """``(peak, rms)`` of ``pcm`` as int16 magnitudes. ``(0, 0)`` when empty.

    Never raises: a malformed buffer reports zero energy, which the caller
    treats as "nothing to transcribe" — the same as silence.
    """
    if not pcm:
        return 0.0, 0.0
    try:
        samples = np.frombuffer(pcm, dtype=np.int16)
    except (ValueError, TypeError):
        return 0.0, 0.0
    if samples.size == 0:
        return 0.0, 0.0
    as_float = samples.astype(np.float32)
    peak = float(np.max(np.abs(as_float)))
    rms = float(np.sqrt(np.mean(np.square(as_float))))
    return peak, rms


def is_silent_segment(pcm: bytes, *, session_peak: float = 0.0) -> bool:
    """Whether ``pcm`` may be closed WITHOUT being transcribed.

    Two independent ways to qualify, and both are conservative by design —
    anything uncertain is transcribed, because a false "silent" verdict deletes
    words permanently while a false "speech" verdict costs one request:

    1. **Absolute.** Quieter than any microphone carrying speech. Needs no
       context and is the one that catches a pause in the very first seconds.
    2. **Relative.** Far below the loudest moment this session has heard, and
       only once that moment was loud enough to have been speech at all.

    ``session_peak`` is the running maximum peak the caller has observed. Pass
    ``0`` to use the absolute test alone.
    """
    peak, rms = segment_energy(pcm)
    if peak <= 0.0:
        return True
    if peak < _CERTAIN_SILENCE_PEAK and rms < _CERTAIN_SILENCE_RMS:
        return True
    return (
        session_peak >= _SPEECH_REFERENCE_PEAK
        and peak < session_peak * _RELATIVE_SILENCE_FRACTION
        and rms < _RELATIVE_SILENCE_RMS
    )


__all__ = [
    "BYTES_PER_SAMPLE",
    "is_silent_segment",
    "quality_windows",
    "quietest_cut",
    "segment_energy",
]
