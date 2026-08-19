"""Wake fanout catch-up batching (``_queue_iter`` coalescing).

Live forensic 2026-07-21 (macOS test machine under parallel build load): the
vosk_kws detector consumed its fanout queue slower than real time, the queue
sat pinned at its 50-chunk cap, and every wake was heard ~5 seconds after it
was spoken — the "inconsistent huge spawn delay". The fix lets a detector
that can consume variable buffer sizes (capability attribute
``coalesce_catchup_s``, resolved by ``_wake_catchup_budget``) drain a BACKLOG
as one concatenated catch-up chunk instead of grinding through it one block
at a time. Pinned here:

- backlog is coalesced up to the cap, byte-for-byte in order;
- an empty/keeping-up queue passes chunks through untouched (hot path is
  byte-identical to the pre-coalescing behavior);
- the default (no capability) never coalesces;
- the end-of-stream sentinel still terminates, even mid-drain.
"""
from __future__ import annotations

import asyncio

from jarvis.core.protocols import AudioChunk
from jarvis.speech.pipeline import _queue_iter

_CHUNK_BYTES = 1600 * 2  # 100 ms of 16 kHz mono int16


def _chunk(fill: int, n_bytes: int = _CHUNK_BYTES, ts: int = 0) -> AudioChunk:
    return AudioChunk(
        pcm=bytes([fill % 256]) * n_bytes, sample_rate=16000, timestamp_ns=ts
    )


async def _drain(it) -> list[AudioChunk]:
    return [c async for c in it]


async def test_backlog_is_coalesced_into_catchup_batches() -> None:
    q: asyncio.Queue = asyncio.Queue()
    chunks = [_chunk(i, ts=i) for i in range(25)]
    for c in chunks:
        q.put_nowait(c)
    q.put_nowait(None)  # sentinel

    out = await _drain(_queue_iter(q, coalesce_max_chunks=10))

    # 25 backlogged chunks drain as 10 + 10 + 5.
    assert [len(c.pcm) // _CHUNK_BYTES for c in out] == [10, 10, 5]
    # Audio is preserved byte-for-byte and in order.
    assert b"".join(c.pcm for c in out) == b"".join(c.pcm for c in chunks)
    # Each batch keeps the timestamp of its FIRST chunk (start of the buffer).
    assert [c.timestamp_ns for c in out] == [0, 10, 20]


async def test_caught_up_stream_passes_chunks_through_untouched() -> None:
    """One chunk at a time in the queue (the keeping-up case) must yield the
    exact same objects as the pre-coalescing iterator — no re-wrapping."""
    q: asyncio.Queue = asyncio.Queue()
    seen: list[AudioChunk] = []

    async def _consume() -> None:
        async for c in _queue_iter(q, coalesce_max_chunks=10):
            seen.append(c)

    task = asyncio.create_task(_consume())
    sent = []
    for i in range(3):
        c = _chunk(i, ts=i)
        sent.append(c)
        q.put_nowait(c)
        # Let the consumer drain before the next chunk arrives.
        for _ in range(5):
            await asyncio.sleep(0)
    q.put_nowait(None)
    await asyncio.wait_for(task, timeout=2.0)
    assert seen == sent  # identity: untouched pass-through


async def test_default_never_coalesces() -> None:
    q: asyncio.Queue = asyncio.Queue()
    for i in range(5):
        q.put_nowait(_chunk(i))
    q.put_nowait(None)
    out = await _drain(_queue_iter(q))
    assert [len(c.pcm) // _CHUNK_BYTES for c in out] == [1, 1, 1, 1, 1]


async def test_sentinel_mid_drain_flushes_and_terminates() -> None:
    q: asyncio.Queue = asyncio.Queue()
    for i in range(3):
        q.put_nowait(_chunk(i))
    q.put_nowait(None)  # sentinel lands inside the first drain window
    q.put_nowait(_chunk(99))  # must never be seen

    out = await _drain(_queue_iter(q, coalesce_max_chunks=10))

    assert len(out) == 1
    assert len(out[0].pcm) == 3 * _CHUNK_BYTES


# --- capability resolver: time budgets -> capture blocks ------------------------
# The batch cap used to be a CHUNK COUNT on the detector; when the capture
# block shrank from 100 ms to 32 ms it silently turned "1 s per batch" into
# 320 ms. Both budgets are seconds now and only the pipeline (which knows the
# block size) turns them into blocks. The deeper intake budget is honoured
# only together with batching (live 2026-08-19: the vosk confirm blocked its
# own consumption for 0.4-0.9 s on a weak CPU, the 0.6 s queue overflowed and
# frames inside the audio about to be verified were dropped).


class _Detector:
    def __init__(self, **attrs):  # noqa: ANN003
        self.__dict__.update(attrs)


def test_catchup_budget_resolves_seconds_to_capture_blocks() -> None:
    from jarvis.audio.capture import capture_chunks_for_duration
    from jarvis.speech.pipeline import _wake_catchup_budget

    coalesce, depth = _wake_catchup_budget(
        _Detector(coalesce_catchup_s=1.0, intake_backlog_s=3.0)
    )
    assert coalesce == capture_chunks_for_duration(1.0)
    assert coalesce > 10  # the old 100 ms-era count would under-batch 32 ms blocks
    assert depth == capture_chunks_for_duration(3.0)


def test_catchup_budget_defaults_without_the_capability() -> None:
    from jarvis.audio.capture import REALTIME_QUEUE_CHUNKS
    from jarvis.speech.pipeline import _wake_catchup_budget

    assert _wake_catchup_budget(_Detector()) == (1, REALTIME_QUEUE_CHUNKS)
    # A deeper backlog WITHOUT batching is refused: a detector that cannot
    # catch up must stay near-present, or a deeper queue is just a later wake.
    assert _wake_catchup_budget(_Detector(intake_backlog_s=5.0)) == (
        1,
        REALTIME_QUEUE_CHUNKS,
    )
    # The intake budget never drops below the generic one.
    coalesce, depth = _wake_catchup_budget(
        _Detector(coalesce_catchup_s=1.0, intake_backlog_s=0.1)
    )
    assert coalesce > 1
    assert depth == REALTIME_QUEUE_CHUNKS


def test_catchup_budget_ignores_garbage_values() -> None:
    from jarvis.audio.capture import REALTIME_QUEUE_CHUNKS
    from jarvis.speech.pipeline import _wake_catchup_budget

    assert _wake_catchup_budget(
        _Detector(coalesce_catchup_s="soon", intake_backlog_s=None)
    ) == (1, REALTIME_QUEUE_CHUNKS)
    coalesce, depth = _wake_catchup_budget(
        _Detector(coalesce_catchup_s=1.0, intake_backlog_s="lots")
    )
    assert coalesce > 1
    assert depth == REALTIME_QUEUE_CHUNKS


def test_vosk_provider_declares_both_budgets() -> None:
    """The live detector is the reason the resolver exists; pin the wiring."""
    from jarvis.audio.capture import REALTIME_QUEUE_CHUNKS
    from jarvis.plugins.wake.vosk_kws_provider import VoskKwsProvider
    from jarvis.speech.pipeline import _wake_catchup_budget

    coalesce, depth = _wake_catchup_budget(VoskKwsProvider)
    assert coalesce > 1
    assert depth > REALTIME_QUEUE_CHUNKS
