"""anyio's thread pool must neither grow nor shrink under the loop (BUG-189).

Starlette runs every synchronous route through ``anyio.to_thread.run_sync``;
anyio starts a worker with ``Thread.start()`` on the calling thread — the event
loop — whenever none is idle, and retires idle workers after ten seconds. On a
host still cold-booting that start blocked the loop for 15 s (2026-08-27).
Like ``test_loop_executor.py`` these assert the pool's SHAPE, not results.
"""

from __future__ import annotations

import threading

import anyio.to_thread
import pytest
from anyio._backends._asyncio import WorkerThread

from jarvis.core.loop_executor import (
    ANYIO_KEEP_ALIVE_S,
    keep_anyio_workers_alive,
    warm_anyio_worker_pool,
)


def test_keep_alive_makes_workers_resident(monkeypatch: pytest.MonkeyPatch) -> None:
    """A worker that retires is a ``Thread.start()`` on the loop later."""
    monkeypatch.setattr(WorkerThread, "MAX_IDLE_TIME", 10)

    assert keep_anyio_workers_alive() is True
    assert WorkerThread.MAX_IDLE_TIME == ANYIO_KEEP_ALIVE_S


def test_keep_alive_is_a_no_op_on_an_unfamiliar_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reshaped anyio must degrade to yesterday's pool, never to a boot crash."""
    monkeypatch.delattr(WorkerThread, "MAX_IDLE_TIME", raising=True)

    assert keep_anyio_workers_alive() is False
    assert not hasattr(WorkerThread, "MAX_IDLE_TIME")


@pytest.mark.asyncio
async def test_warm_up_brings_up_distinct_workers_that_serve_the_next_call() -> None:
    """Four parked calls need four threads — one worker cannot serve them all —
    and once they are resident a real call starts nothing new."""
    resident = await warm_anyio_worker_pool(4)
    assert resident == 4

    before = threading.active_count()
    await anyio.to_thread.run_sync(lambda: None)
    assert threading.active_count() == before, "the call had to start a thread"
