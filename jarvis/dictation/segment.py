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
    except (ValueError, TypeError):  # An incomplete PCM tail uses the aligned boundary.
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

#: Audio the two halves of a re-read share. Same job as the overlap between
#: quality windows: a word sitting on the seam is spoken in full inside one of
#: them, and the duplicate that creates is removed from the TEXT afterwards.
#: Small, because the cut already went looking for the quietest moment.
_REREAD_OVERLAP_S = 0.4


def halve_at_quietest(
    pcm: bytes,
    *,
    bytes_per_second: int = 16_000 * BYTES_PER_SAMPLE,
    overlap_s: float = _REREAD_OVERLAP_S,
) -> list[tuple[int, int]]:
    """Two overlapping halves of ``pcm``, split at its quietest middle moment.

    The re-read shape for a window a recognizer stopped short on WITHOUT
    leaving the energy scan a sustained pause to split at — continuous speech
    that simply arrived half-transcribed. :func:`speech_runs` reports one run
    for that recording and has nothing to offer; halving it does, because the
    second request is handed audio that begins where the delivered text went
    quiet, and a recognizer that truncates a long read rarely truncates a short
    one at the same place.

    Not a substitute for the pause split: a recording that HAS pauses should be
    cut there, since those seams cost nothing. This is the fallback for the one
    that does not.

    Returns ``[]`` when the buffer is too short for the split to mean anything,
    which the caller reads as "keep what the recognizer said".
    """
    total = len(pcm)
    min_half = int(_MIN_WINDOW_S * bytes_per_second)
    if total < 2 * min_half:
        return []
    cut = quietest_cut(pcm, _align(total // 2), bytes_per_second)
    if cut < min_half or total - cut < min_half:
        # The quiet scan landed somewhere that would leave a sliver. The plain
        # midpoint is no worse a guess and keeps both halves worth reading.
        cut = _align(total // 2)
    overlap = _align(max(0, int(overlap_s * bytes_per_second)))
    return [
        (0, _align(min(total, cut + overlap))),
        (_align(max(0, cut - overlap)), total),
    ]


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
    ranges: list[tuple[int, int]] = []
    start = 0
    while start < total:
        window, start = next_quality_window(
            pcm[start:],
            start=start,
            total=total,
            window_bytes=window_bytes,
            overlap_bytes=overlap_bytes,
            bytes_per_second=bytes_per_second,
        )
        ranges.append(window)
    return ranges


def next_quality_window(
    scan: bytes,
    *,
    start: int,
    total: int,
    window_bytes: int,
    overlap_bytes: int = 0,
    bytes_per_second: int = 16_000 * BYTES_PER_SAMPLE,
) -> tuple[tuple[int, int], int]:
    """One step of :func:`quality_windows`: the window at ``start`` and where
    the next one begins.

    ``scan`` is the audio FROM ``start`` onward — at least one window of it
    when the recording holds that much; ``total`` is the recording's length in
    bytes. Returns ``((start, end), next_start)``; ``next_start == total`` when
    this was the last window.

    Public because the dictation lane closes final windows WHILE the user is
    still speaking, one per tick, so that on key release only the open tail is
    left to read. Sharing this step is what keeps that incremental shape
    byte-identical to the one-shot ``quality_windows`` over the finished
    recording: the cut depends only on the audio inside the window, so a
    window closed mid-recording is the very window the finished recording
    would have produced.
    """
    window = _align(window_bytes)
    if window <= 0:
        return (start, total), total
    # More than half a window of overlap can never improve a seam enough to
    # justify re-reading most of the same audio. More importantly, clamping to
    # ``window - one sample`` technically made progress but could turn a bad
    # 5s/5s configuration into millions of two-byte windows. Half a window is
    # still far above the recommended 1--2 seconds and gives a hard O(n) bound.
    overlap = max(0, min(_align(overlap_bytes), window // 2))
    min_window = int(_MIN_WINDOW_S * bytes_per_second)
    remaining = total - start
    if remaining <= window:
        return (start, total), total
    cut = quietest_cut(scan, window, bytes_per_second)
    if cut < min_window:
        # The scan found its quiet spot too early to be a useful window —
        # take the nominal length instead. The overlap is what protects the
        # word this cut may land inside.
        cut = window
    end = _align(min(total, start + cut))
    if end <= start:  # pragma: no cover — _align keeps this unreachable
        end = min(total, start + window)
    # Step back by the overlap, but never to (or behind) this window's own
    # start: that would transcribe the same audio forever.
    # A pause search may cut as early as 60% of the nominal window. Keep a
    # half-window minimum stride even then: otherwise a legal 50% overlap
    # plus the earliest cut advances by only 10% and multiplies requests.
    return (start, end), max(end - overlap, start + window // 2)


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


#: Frame width used when scanning a window for sustained pauses. Short enough
#: to place a pause edge within a syllable, long enough that one frame's RMS is
#: a meaningful loudness reading.
_RUN_FRAME_S = 0.05

#: A quiet gap must last at least this long before it splits two speech runs.
#: Inter-word pauses are shorter; this is the "breath between sentences" scale
#: — the pause length that makes a cloud recognizer stop transcribing.
_RUN_MIN_PAUSE_S = 0.6

#: Audio kept around each run so a soft onset or trailing consonant is not
#: clipped off by the frame grid.
_RUN_PAD_S = 0.15


def speech_runs(
    pcm: bytes,
    *,
    session_peak: float = 0.0,
    bytes_per_second: int = 16_000 * BYTES_PER_SAMPLE,
    min_pause_s: float = _RUN_MIN_PAUSE_S,
    pad_s: float = _RUN_PAD_S,
) -> list[tuple[int, int]]:
    """Byte ranges of continuous speech in ``pcm``, split at sustained pauses.

    The energy-only answer to "does this recording contain a mid-recording
    pause, and where does the speech on each side of it live?". Quiet gaps
    shorter than ``min_pause_s`` stay inside their run — they are the pauses
    between words, and cutting there would hand a recognizer half-words. Each
    run is padded by ``pad_s`` on both sides and clamped to the buffer.

    Quiet is judged with the same two tests as :func:`is_silent_segment` —
    absolute room-tone floor plus far-below-``session_peak`` — on ENERGY only,
    never on transcribed content (the wake path's AP-27 lesson). Returns ``[]``
    when nothing in ``pcm`` reaches speech loudness, and one whole-buffer run
    is simply a recording without a sustained pause.
    """
    if not pcm:
        return []
    frame_bytes = _align(max(BYTES_PER_SAMPLE, int(_RUN_FRAME_S * bytes_per_second)))
    try:
        samples = np.frombuffer(
            pcm[: len(pcm) - (len(pcm) % BYTES_PER_SAMPLE)], dtype=np.int16
        ).astype(np.float32)
    except (ValueError, TypeError):  # Invalid PCM cannot yield trustworthy speech spans.
        return []
    if samples.size == 0:
        return []
    frame_samples = max(1, frame_bytes // BYTES_PER_SAMPLE)
    frame_count = int(np.ceil(samples.size / frame_samples))
    padded = np.zeros(frame_count * frame_samples, dtype=np.float32)
    padded[: samples.size] = samples
    frames = padded.reshape(frame_count, frame_samples)
    peaks = np.max(np.abs(frames), axis=1)
    rms = np.sqrt(np.mean(np.square(frames), axis=1))

    quiet = (peaks < _CERTAIN_SILENCE_PEAK) & (rms < _CERTAIN_SILENCE_RMS)
    if session_peak >= _SPEECH_REFERENCE_PEAK:
        quiet |= (peaks < session_peak * _RELATIVE_SILENCE_FRACTION) & (rms < _RELATIVE_SILENCE_RMS)

    speech_frames = np.flatnonzero(~quiet)
    if speech_frames.size == 0:
        return []

    min_pause_frames = max(1, int(round(min_pause_s / _RUN_FRAME_S)))
    runs: list[tuple[int, int]] = []  # in frame indices, inclusive
    start = int(speech_frames[0])
    prev = start
    for frame in speech_frames[1:]:
        idx = int(frame)
        if idx - prev - 1 >= min_pause_frames:
            runs.append((start, prev))
            start = idx
        prev = idx
    runs.append((start, prev))

    pad_bytes = _align(max(0, int(pad_s * bytes_per_second)))
    ranges: list[tuple[int, int]] = []
    for first, last in runs:
        begin = max(0, first * frame_bytes - pad_bytes)
        end = min(len(pcm), (last + 1) * frame_bytes + pad_bytes)
        begin, end = _align(begin), _align(end)
        if ranges and begin <= ranges[-1][1]:
            # Padding bridged the gap — the pause was barely over the
            # threshold, and two touching runs are one run.
            ranges[-1] = (ranges[-1][0], end)
        else:
            ranges.append((begin, end))
    return ranges


def segment_energy(pcm: bytes) -> tuple[float, float]:
    """``(peak, rms)`` of ``pcm`` as int16 magnitudes. ``(0, 0)`` when empty.

    Never raises: a malformed buffer reports zero energy, which the caller
    treats as "nothing to transcribe" — the same as silence.
    """
    if not pcm:
        return 0.0, 0.0
    try:
        samples = np.frombuffer(pcm, dtype=np.int16)
    except (ValueError, TypeError):  # Invalid PCM has no measurable signal level.
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
    "halve_at_quietest",
    "is_silent_segment",
    "quality_windows",
    "quietest_cut",
    "segment_energy",
    "speech_runs",
]
