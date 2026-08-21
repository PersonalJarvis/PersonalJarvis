"""Capture queue overflow keeps the FRESHEST audio (drop-oldest).

Regression for the weak-laptop root cause: a consumer that cannot run the inline
per-frame VAD inference in real time backs the capture queue up. The old policy
dropped the NEWEST chunk, so the consumer processed a growing STALE backlog and
saw the current end-of-speech silence (and the wake word) seconds late. The queue
now drops the OLDEST chunk, bounding staleness to the queue depth, and a
real-time detection consumer uses a shallow depth so the bound is small.
"""
from __future__ import annotations

from jarvis.audio.capture import (
    BLOCKSIZE,
    DEFAULT_QUEUE_CHUNKS,
    REALTIME_QUEUE_CHUNKS,
    SAMPLE_RATE,
    MicrophoneCapture,
)
from jarvis.core.protocols import AudioChunk


def _chunk(tag: int) -> AudioChunk:
    return AudioChunk(
        pcm=bytes([tag & 0xFF]) * 4,
        sample_rate=16_000,
        timestamp_ns=tag,
        channels=1,
    )


def test_realtime_depth_is_shallower_than_the_bulk_default() -> None:
    assert MicrophoneCapture()._queue.maxsize == DEFAULT_QUEUE_CHUNKS
    assert (
        MicrophoneCapture(max_queue_chunks=REALTIME_QUEUE_CHUNKS)._queue.maxsize
        == REALTIME_QUEUE_CHUNKS
    )
    assert 1 <= REALTIME_QUEUE_CHUNKS < DEFAULT_QUEUE_CHUNKS
    assert DEFAULT_QUEUE_CHUNKS * BLOCKSIZE / SAMPLE_RATE >= 2.0
    assert REALTIME_QUEUE_CHUNKS * BLOCKSIZE / SAMPLE_RATE >= 0.6


def test_capture_block_matches_the_native_vad_frame_budget() -> None:
    assert BLOCKSIZE == 512
    assert BLOCKSIZE / SAMPLE_RATE == 0.032


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


# --------------------------------------------------------------------------
# Re-bounding a live stream — the wake mic becomes a dictation mic mid-flight
# --------------------------------------------------------------------------


def test_the_depth_can_be_raised_while_the_stream_is_open() -> None:
    """A bulk consumer takes a shallow stream over and deepens it.

    Not a hypothetical: with a wake word configured, EVERY dictation borrows
    the wake stream rather than opening its own, so the deep bulk depth written
    for dictation only ever applied on installs with no wake word at all.
    """
    mic = MicrophoneCapture(max_queue_chunks=REALTIME_QUEUE_CHUNKS)

    previous = mic.set_queue_depth(DEFAULT_QUEUE_CHUNKS)

    assert previous == REALTIME_QUEUE_CHUNKS
    assert mic.queue_depth == DEFAULT_QUEUE_CHUNKS
    assert mic.set_queue_depth(previous) == DEFAULT_QUEUE_CHUNKS
    assert mic.queue_depth == REALTIME_QUEUE_CHUNKS


def test_a_deepened_queue_stops_dropping_what_the_shallow_one_lost() -> None:
    """The whole point: the frames a stall would have deleted are still there."""
    mic = MicrophoneCapture(max_queue_chunks=2)
    mic.set_queue_depth(4)

    for tag in (1, 2, 3, 4):
        mic._safe_put(_chunk(tag))

    drained = []
    while not mic._queue.empty():
        drained.append(mic._queue.get_nowait().timestamp_ns)

    assert drained == [1, 2, 3, 4], drained
    assert mic.dropped_frames == 0


def test_the_queue_object_survives_a_resize() -> None:
    """Identity matters: a consumer parked in ``get()`` holds the old object.

    Building a fresh queue and copying the items across would leave the
    dictation's drain waiting on a queue nothing puts to again — the recording
    would simply stop, which is the failure this whole change is about.
    """
    mic = MicrophoneCapture(max_queue_chunks=2)
    before = mic._queue
    mic._safe_put(_chunk(7))

    mic.set_queue_depth(9)

    assert mic._queue is before
    assert mic._queue.get_nowait().timestamp_ns == 7


def test_a_depth_below_one_is_refused() -> None:
    """``asyncio.Queue(0)`` means UNBOUNDED — the opposite of a tight bound."""
    mic = MicrophoneCapture(max_queue_chunks=5)

    mic.set_queue_depth(0)
    assert mic.queue_depth == 1

    mic.set_queue_depth(-3)
    assert mic.queue_depth == 1


def test_safe_put_no_drop_when_not_full() -> None:
    mic = MicrophoneCapture(max_queue_chunks=8)
    for tag in range(5):
        mic._safe_put(_chunk(tag))
    assert mic.dropped_frames == 0
    assert mic._queue.qsize() == 5
