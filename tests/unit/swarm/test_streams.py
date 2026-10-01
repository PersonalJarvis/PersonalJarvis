"""Large downloads stream incrementally and close promptly on revocation."""

import asyncio
import hashlib
from contextlib import aclosing

import pytest

from jarvis.swarm.store import SwarmAccessError, SwarmStoreError
from jarvis.swarm.streams import acquire_stream, verified_chunks
from tests.fakes.swarm_streams import BoundedStream


@pytest.mark.asyncio
async def test_stream_reads_bounded_chunks_and_holds_unverified_tail():
    data = b"stream" * 50000
    source = BoundedStream(data)
    record = {"size_bytes": str(len(data)), "sha256": hashlib.sha256(data).hexdigest()}
    result = [chunk async for chunk in verified_chunks(source, record, asyncio.to_thread)]
    assert b"".join(result) == data
    assert len(result) > 1 and max(map(len, result)) == 65536
    assert source.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked_read", [1, 2])
async def test_disconnect_waits_for_active_read_before_closing(blocked_read):
    import threading

    from jarvis.swarm.concurrency import SwarmExecutors

    entered, release = threading.Event(), threading.Event()
    data = b"bounded-stream" * 10000

    class DelayedStream(BoundedStream):
        active_read = False
        concurrent_close = False
        reads = 0

        def read(self, size=-1):
            self.reads += 1
            if self.reads != blocked_read:
                return super().read(size)
            self.active_read = True
            entered.set()
            try:
                assert release.wait(5)
                return super().read(size)
            finally:
                self.active_read = False

        def close(self):
            self.concurrent_close = self.active_read
            super().close()

    source = DelayedStream(data)
    record = {"size_bytes": str(len(data)), "sha256": hashlib.sha256(data).hexdigest()}
    storage = SwarmExecutors()
    stream = verified_chunks(source, record, storage.transfer)
    task = asyncio.create_task(anext(stream))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        assert not source.closed
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert source.closed
        assert not source.concurrent_close
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await stream.aclose()
        await storage.close()


@pytest.mark.asyncio
async def test_corrupt_stream_never_delivers_its_final_chunk():
    data = b"changed" * 20000
    source = BoundedStream(data)
    record = {"size_bytes": str(len(data)), "sha256": "0" * 64}
    received = []
    with pytest.raises(SwarmStoreError, match="hash"):
        async for chunk in verified_chunks(source, record, asyncio.to_thread):
            received.append(chunk)
    assert sum(map(len, received)) < len(data)
    assert source.closed


@pytest.mark.asyncio
async def test_scope_revocation_and_consumer_disconnect_close_stream():
    data = b"private" * 30000
    source = BoundedStream(data)
    record = {"size_bytes": str(len(data)), "sha256": hashlib.sha256(data).hexdigest()}
    calls = 0

    def authorize():
        nonlocal calls
        calls += 1
        if calls > 1:
            raise SwarmAccessError("Revoked")

    with pytest.raises(SwarmAccessError):
        async for _ in verified_chunks(source, record, asyncio.to_thread, authorize=authorize):
            pass
    assert source.closed
    second = BoundedStream(data)
    async with aclosing(verified_chunks(second, record, asyncio.to_thread)) as stream:
        await anext(stream)
    assert second.closed


@pytest.mark.asyncio
async def test_cancellation_during_open_retains_ownership_until_handle_closes():
    import threading

    entered, release = threading.Event(), threading.Event()
    source = BoundedStream(b"late-opened")

    def delayed_open():
        entered.set()
        assert release.wait(5)
        return source

    task = asyncio.create_task(acquire_stream(asyncio.to_thread, delayed_open))
    assert await asyncio.to_thread(entered.wait, 5)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert source.closed
