"""Capture queue overflow keeps the FRESHEST audio (drop-oldest).

Regression for the weak-laptop root cause: a consumer that cannot run the inline
per-frame VAD inference in real time backs the capture queue up. The old policy
dropped the NEWEST chunk, so the consumer processed a growing STALE backlog and
saw the current end-of-speech silence (and the wake word) seconds late. The queue
now drops the OLDEST chunk, bounding staleness to the queue depth, and a
real-time detection consumer uses a shallow depth so the bound is small.
"""
from __future__ import annotations

from jarvis.audio.capture import REALTIME_QUEUE_CHUNKS, MicrophoneCapture
from jarvis.core.protocols import AudioChunk


def _chunk(tag: int) -> AudioChunk:
    return AudioChunk(
        pcm=bytes([tag & 0xFF]) * 4,
        sample_rate=16_000,
        timestamp_ns=tag,
        channels=1,
    )


def test_realtime_depth_is_shallower_than_the_bulk_default() -> None:
    assert MicrophoneCapture()._queue.maxsize == 20
    assert (
        MicrophoneCapture(max_queue_chunks=REALTIME_QUEUE_CHUNKS)._queue.maxsize
        == REALTIME_QUEUE_CHUNKS
    )
    assert 1 <= REALTIME_QUEUE_CHUNKS < 20


def test_safe_put_drops_oldest_and_keeps_newest_on_overflow() -> None:
    mic = MicrophoneCapture(max_queue_chunks=3)
    for tag in (1, 2, 3):
        mic._safe_put(_chunk(tag))  # fill to capacity
    mic._safe_put(_chunk(4))        # overflow → drop the oldest (1), keep newest

    drained = []
    while not mic._queue.empty():
        drained.append(mic._queue.get_nowait().timestamp_ns)

    assert drained == [2, 3, 4], drained  # oldest (1) dropped, present preserved
    assert mic.dropped_frames == 1


def test_safe_put_no_drop_when_not_full() -> None:
    mic = MicrophoneCapture(max_queue_chunks=8)
    for tag in range(5):
        mic._safe_put(_chunk(tag))
    assert mic.dropped_frames == 0
    assert mic._queue.qsize() == 5
