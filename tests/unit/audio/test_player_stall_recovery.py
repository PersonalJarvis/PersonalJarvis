"""Wave-1 latency fix: audio-playback write-progress + device-stall recovery.

These guard the dominant 60-156 s voice-hang root cause: a wedged PortAudio
``stream.write`` that previously could only be escaped by a 120 s ceiling. The
player now records write progress and exposes ``abort_active`` so the pipeline
watchdog can unblock a wedged device in ~5 s instead of 120 s.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator

import numpy as np
import pytest

from jarvis.audio.player import AudioPlayer
from jarvis.core.protocols import AudioChunk


class _FakeStream:
    """Stand-in for sd.OutputStream: write() can be made to block forever."""

    def __init__(self) -> None:
        self.aborted = False
        self.closed = False
        self._block_forever = False

    def write(self, chunk):  # noqa: ANN001
        if self._block_forever:
            while self._block_forever and not self.aborted:
                time.sleep(0.01)
        return False  # not underflowed

    def abort(self) -> None:
        self.aborted = True
        self._block_forever = False

    def close(self) -> None:
        self.closed = True


def test_write_progress_advances_on_each_subblock() -> None:
    player = AudioPlayer.__new__(AudioPlayer)
    player._init_progress()
    stream = _FakeStream()
    arr = np.zeros(48_000, dtype=np.int16)  # 1 s @ 48 kHz mono
    before_frames = player.frames_written
    before_ns = player.last_write_ns

    player._write_samples(stream, arr, 48_000, 48_000)

    assert player.frames_written > before_frames
    assert player.last_write_ns > before_ns


def test_abort_active_unblocks_wedged_stream() -> None:
    player = AudioPlayer.__new__(AudioPlayer)
    player._init_progress()
    stream = _FakeStream()
    player._active_stream = stream

    player.abort_active()

    assert stream.aborted is True
    assert stream.closed is True
    assert player._active_stream is None


def test_abort_active_is_idempotent_with_no_stream() -> None:
    player = AudioPlayer.__new__(AudioPlayer)
    player._init_progress()
    player._active_stream = None
    # Must not raise when there is nothing to abort.
    player.abort_active()
    assert player._active_stream is None


async def _no_chunks() -> AsyncIterator[AudioChunk]:
    """An empty TTS stream — play_chunks must not touch the audio device."""
    return
    yield  # pragma: no cover - makes this an async generator


@pytest.mark.asyncio
async def test_play_chunks_resets_progress_at_start() -> None:
    """play_chunks must zero last_write_ns at the START of every playback.

    Root cause of the "Jarvis listens forever / does nothing" bug: last_write_ns
    was only zeroed once in _init_progress() and then carried the PREVIOUS turn's
    timestamp across turns. The pipeline stall watchdog reads it, so after a >5 s
    thinking gap it saw a stale-but-non-zero value and aborted the fresh answer
    before its first frame (a false "device-wedge"). Resetting per playback
    restores the <=0 "no first frame yet" guard the watchdog relies on.
    """
    player = AudioPlayer.__new__(AudioPlayer)
    player._init_progress()
    player._device_logged = True  # skip device query in _log_device_once
    player._play_lock = None
    # Simulate the leftover progress of a PREVIOUS turn.
    player.last_write_ns = time.monotonic_ns() - int(10e9)  # 10 s ago
    player.frames_written = 999

    await player.play_chunks(_no_chunks())

    assert player.last_write_ns == 0
    assert player.frames_written == 0


@pytest.mark.asyncio
async def test_play_chunks_stamps_progress_owner_at_start() -> None:
    """The pipeline watchdog can only ignore unrelated playback progress if the
    real player stamps which ``play_chunks`` task owns the current progress
    counter."""
    player = AudioPlayer.__new__(AudioPlayer)
    player._init_progress()
    player._device_logged = True
    player._play_lock = None

    task = asyncio.current_task()
    assert task is not None

    await player.play_chunks(_no_chunks())

    assert player.last_write_owner_task_id == id(task)


@pytest.mark.asyncio
async def test_play_chunks_resets_progress_before_lock_wait() -> None:
    """The reset must happen BEFORE awaiting the play lock.

    If a concurrent op (e.g. an ack via play_pcm, or a slow stream-open) holds
    ``_play_lock``, play_chunks must STILL present last_write_ns == 0 to the
    watchdog from its first event-loop tick — a stale value visible during the
    lock wait would re-introduce the false "device-wedge" abort.
    """
    player = AudioPlayer.__new__(AudioPlayer)
    player._init_progress()
    player._device_logged = True
    player._play_lock = None
    player.last_write_ns = time.monotonic_ns() - int(10e9)  # stale previous turn
    player.frames_written = 999

    lock = player._get_play_lock()
    await lock.acquire()  # a concurrent playback is holding the lock
    task = None
    try:
        task = asyncio.create_task(player.play_chunks(_no_chunks()))
        await asyncio.sleep(0.05)  # let play_chunks run up to the (blocked) lock
        # The reset ran before the lock wait, even though the lock is still held.
        assert player.last_write_ns == 0
        assert player.frames_written == 0
    finally:
        lock.release()
        if task is not None:
            await task
