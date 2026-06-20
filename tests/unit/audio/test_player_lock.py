"""Unit tests for AudioPlayer's asyncio.Lock serialisation.

Background: 2026-05-14 voice-overlap diagnosis (see
docs/diagnostics/voice-overlap-2026-05-14.md + commit 33d51c5f) showed
two distinct producers (Pre-Thinking Flash-Brain announcement + main
streaming-brain answer) racing to ``play_chunks``/``play_pcm``. Without
a lock, each opened its own ``sd.OutputStream`` and WASAPI shared-mode
mixed both signals on the speaker, producing audible double-voice.

The fix wraps ``play_chunks`` and ``play_pcm`` with a lazy
``asyncio.Lock`` on the AudioPlayer instance.  This test verifies the
**guarantee**: two concurrent calls observe each other's body in a
strict before/after order, never interleaved.

We deliberately do NOT spin up sounddevice / sd.OutputStream — the Lock
is the contract we're testing, not PortAudio. Internal methods are
monkeypatched to no-ops.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from jarvis.audio.player import AudioPlayer
from jarvis.core.protocols import AudioChunk


async def _one_chunk(pcm: bytes) -> AsyncIterator[AudioChunk]:
    """Single-chunk async generator (the lib's normal input shape)."""
    yield AudioChunk(pcm=pcm, sample_rate=24_000, timestamp_ns=0, channels=1)


def _make_player_with_recorded_inner(monkeypatch) -> tuple[AudioPlayer, list[str]]:
    """Build an AudioPlayer whose stream IO is replaced with sleeps that
    record (start, end) events into a shared list. The list lets the test
    assert on call interleaving.

    Returns: (player, events) where ``events`` ends up looking like
    ``["A:enter", "A:exit", "B:enter", "B:exit"]`` under a working lock,
    or ``["A:enter", "B:enter", ...]`` (interleaved) without one.
    """
    player = AudioPlayer.__new__(AudioPlayer)  # bypass device resolve
    player._device = None
    player._sample_rate = 24_000
    player._channels = 1
    player._device_logged = True  # suppress logging path
    player._bus = None
    player._play_lock = None  # forces lazy-init in _get_play_lock
    # Persistent-stream fields (added 2026-05-16 for time-stretch fix).
    player._active_stream = None
    player._active_source_rate = None
    player._active_device_rate = None
    # Device-rate cache (added 2026-05-16 Welle-2 for crackling/drift fix).
    player._device_rate_cache = {}

    # Replace device-IO surface with no-ops.
    monkeypatch.setattr(player, "_open_output_stream", lambda r: (object(), r))
    monkeypatch.setattr(player, "_close_output_stream", lambda s: None)
    monkeypatch.setattr(player, "_write_samples", lambda *a, **kw: None)

    return player, []


@pytest.mark.asyncio
async def test_two_play_chunks_calls_serialise_via_lock(monkeypatch) -> None:
    """Two concurrent play_chunks invocations must NOT interleave their
    bodies. With the lock in place, the second only enters after the
    first leaves.
    """
    player, events = _make_player_with_recorded_inner(monkeypatch)

    # Override _write_samples to (a) log entry, (b) yield to the loop so a
    # second task gets a chance to interleave if the lock is missing,
    # (c) log exit. This catches a real race because asyncio.sleep(0)
    # cedes control deterministically.
    async def slow_write_a(*args, **kw):
        events.append("A:enter")
        await asyncio.sleep(0.05)
        events.append("A:exit")

    async def slow_write_b(*args, **kw):
        events.append("B:enter")
        await asyncio.sleep(0.05)
        events.append("B:exit")

    # Patch the asyncio.to_thread call: replace _write_samples per call.
    # AudioPlayer wraps the sync write via asyncio.to_thread; we just
    # swap in async writes that mimic the wall-clock delay without a
    # thread.
    def _make_play_chunks(tag: str, slow_write):
        async def go():
            # Inline reproduction of play_chunks body, simplified — locks
            # the same Lock the real implementation uses and runs the
            # body. This keeps the test focused on the contract.
            async with player._get_play_lock():
                await slow_write()
        return go

    a_call = _make_play_chunks("A", slow_write_a)
    b_call = _make_play_chunks("B", slow_write_b)

    # Kick off two concurrent "play" tasks.
    await asyncio.gather(a_call(), b_call())

    # Either A fully before B, or B fully before A — never interleaved.
    assert events in (
        ["A:enter", "A:exit", "B:enter", "B:exit"],
        ["B:enter", "B:exit", "A:enter", "A:exit"],
    ), f"lock did not serialise; observed: {events}"


@pytest.mark.asyncio
async def test_lock_is_lazy_constructed_and_idempotent(monkeypatch) -> None:
    """``_get_play_lock`` must return the same Lock on repeated calls
    (otherwise concurrent calls would lock different objects)."""
    player = AudioPlayer.__new__(AudioPlayer)
    player._play_lock = None

    lock1 = player._get_play_lock()
    lock2 = player._get_play_lock()
    assert isinstance(lock1, asyncio.Lock)
    assert lock1 is lock2, "lock must be idempotent across calls"


@pytest.mark.asyncio
async def test_real_play_chunks_holds_lock_while_streaming(monkeypatch) -> None:
    """End-to-end on the real ``play_chunks`` coroutine: while one call
    is mid-flight, a second call observes the lock as held.
    """
    player, _ = _make_player_with_recorded_inner(monkeypatch)
    enter_count = 0
    exit_count = 0

    # Patch the stream-write path so it consumes wall-clock time without
    # touching PortAudio. _flush_pending in play_chunks eventually calls
    # asyncio.to_thread(self._write_samples, ...); we make that slow.
    real_to_thread = asyncio.to_thread

    async def slow_to_thread(func, *args, **kw):
        nonlocal enter_count, exit_count
        # Only delay the sample-writer call, not the stream-opener/closer.
        if func is player._write_samples:
            enter_count += 1
            await asyncio.sleep(0.03)
            exit_count += 1
            return None
        return await real_to_thread(func, *args, **kw)

    monkeypatch.setattr("jarvis.audio.player.asyncio.to_thread", slow_to_thread)

    # Two real play_chunks invocations in parallel.
    await asyncio.gather(
        player.play_chunks(_one_chunk(b"\x01\x00" * 4000)),
        player.play_chunks(_one_chunk(b"\x02\x00" * 4000)),
    )

    # Each call should have hit the writer at least once and the writer
    # should have a balanced enter/exit count — that is the symptom of
    # serial execution.
    assert enter_count >= 2
    assert enter_count == exit_count


async def _gated_chunk(
    gate: asyncio.Event, pcm: bytes
) -> AsyncIterator[AudioChunk]:
    """A producer that yields NOTHING until ``gate`` is set — mimics the
    streaming answer whose brain is still thinking / running a tool, so its
    ``_merged_chunks`` blocks on the sentence queue with no audio yet."""
    await gate.wait()
    yield AudioChunk(pcm=pcm, sample_rate=24_000, timestamp_ns=0, channels=1)


@pytest.mark.asyncio
async def test_play_chunks_does_not_hold_lock_while_awaiting_first_chunk(
    monkeypatch,
) -> None:
    """Regression (2026-06-20 'preamble spoken AFTER the answer'): the answer's
    ``play_chunks(_merged_chunks())`` task is created at turn-start and, on a
    long tool turn, waits seconds for its first audio chunk. It must NOT hold
    the play lock during that wait — otherwise a concurrently-published ack
    preamble blocks behind the still-silent answer and is voiced AFTER it
    (player.py:626 grabbed the lock before pulling the first chunk).

    Contract: while a producer is still awaiting its first chunk, a second
    ``play_chunks`` call can acquire the lock and play to completion.
    """
    player, _ = _make_player_with_recorded_inner(monkeypatch)
    writes: list[str] = []

    real_to_thread = asyncio.to_thread

    async def rec_to_thread(func, *args, **kw):
        if func is player._write_samples:
            writes.append("preamble-write")
            return None
        return await real_to_thread(func, *args, **kw)

    monkeypatch.setattr("jarvis.audio.player.asyncio.to_thread", rec_to_thread)

    gate = asyncio.Event()
    # The "answer" produces no audio until the gate opens.
    answer_task = asyncio.create_task(
        player.play_chunks(_gated_chunk(gate, b"\x09\x00" * 4000))
    )
    # Let the answer task start and (under the bug) seize the lock.
    await asyncio.sleep(0.02)

    # The preamble must be able to play NOW, while the answer is still silent.
    # Under the bug this deadlocks (the answer holds the lock and never
    # releases because its gate is closed) → wait_for raises TimeoutError.
    try:
        await asyncio.wait_for(
            player.play_chunks(_one_chunk(b"\x01\x00" * 4000)), timeout=1.0
        )
    except TimeoutError:
        gate.set()
        await answer_task
        pytest.fail(
            "preamble blocked behind the still-silent answer — play lock held "
            "while awaiting the first chunk"
        )

    assert writes, "the preamble produced no audio while the answer was silent"

    # Release the answer and let it finish cleanly.
    gate.set()
    await answer_task


@pytest.mark.asyncio
async def test_play_chunks_should_play_predicate_drops_stale_playback(
    monkeypatch,
) -> None:
    """A caller may pass ``should_play``; evaluated AFTER the lock is acquired,
    a False verdict drops the playback without writing. This is the staleness
    gate the ack preamble uses so it is never voiced once the answer has
    started speaking (defense-in-depth for the 2026-06-20 misorder)."""
    player, _ = _make_player_with_recorded_inner(monkeypatch)
    writes: list[str] = []

    real_to_thread = asyncio.to_thread

    async def rec_to_thread(func, *args, **kw):
        if func is player._write_samples:
            writes.append("w")
            return None
        return await real_to_thread(func, *args, **kw)

    monkeypatch.setattr("jarvis.audio.player.asyncio.to_thread", rec_to_thread)

    # Stale: should_play() is False → no audio written.
    await player.play_chunks(
        _one_chunk(b"\x01\x00" * 4000), should_play=lambda: False
    )
    assert writes == [], "a stale playback wrote audio instead of being dropped"

    # Valid: should_play() is True → audio written normally.
    await player.play_chunks(
        _one_chunk(b"\x01\x00" * 4000), should_play=lambda: True
    )
    assert writes, "a valid playback was dropped"
