"""Guards for the prewarmed default executor.

The bug these protect against is invisible in a functional test: a pool that
grows lazily still returns the right answers, it just stops the event loop for
as long as ``Thread.start()`` takes while doing it (measured 75.7 s on a loaded
host, 2026-07-29). So the assertions here are about the pool's SHAPE — that
every worker exists before the loop asks for one — not about results.
"""

from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from jarvis.core.loop_executor import (
    MAX_WORKERS,
    MIN_WORKERS,
    default_worker_count,
    install_prewarmed_default_executor,
    prewarm,
)


class TestWorkerCount:
    def test_a_tiny_host_still_gets_the_floor(self):
        """A 2-core VPS runs the same blocking call sites as a workstation.
        CPython's ``cpu_count + 4`` would put it at 6 and reintroduce growth."""
        assert default_worker_count(cpu_count=2) == MIN_WORKERS

    def test_a_large_host_is_capped(self):
        assert default_worker_count(cpu_count=128) == MAX_WORKERS

    def test_an_unknown_core_count_does_not_crash(self):
        assert MIN_WORKERS <= default_worker_count(cpu_count=None) <= MAX_WORKERS


class TestPrewarm:
    def test_every_worker_exists_afterwards(self):
        """The whole point: once this returns, ``_adjust_thread_count`` can no
        longer reach ``Thread.start()``, because the pool is already full."""
        pool = ThreadPoolExecutor(max_workers=8)
        try:
            prewarm(pool, 8).join(timeout=30)
            assert len(pool._threads) == 8
        finally:
            pool.shutdown(wait=False)

    def test_it_does_not_block_the_caller(self):
        """Filling the pool is the blocking work; doing it on the caller's
        thread would just move the stall onto the boot path (AP-26)."""
        pool = ThreadPoolExecutor(max_workers=16)
        try:
            started = time.monotonic()
            thread = prewarm(pool, 16)
            assert time.monotonic() - started < 0.5
            thread.join(timeout=30)
        finally:
            pool.shutdown(wait=False)

    def test_workers_are_released_again(self):
        """A prewarm that parked its workers forever would hand the loop a pool
        with nothing free — the same stall, one step later."""
        pool = ThreadPoolExecutor(max_workers=4)
        try:
            prewarm(pool, 4).join(timeout=30)
            done = threading.Event()
            pool.submit(done.set)
            assert done.wait(timeout=10), "prewarm left the pool occupied"
        finally:
            pool.shutdown(wait=False)

    def test_a_shut_down_pool_is_survived(self):
        pool = ThreadPoolExecutor(max_workers=2)
        pool.shutdown(wait=True)
        prewarm(pool, 2).join(timeout=10)  # must not raise


class TestInstall:
    def test_to_thread_never_grows_the_pool_afterwards(self):
        """The regression itself. A lazily-grown pool reaches ``Thread.start()``
        from ``run_in_executor``, ON the loop; a prewarmed one cannot."""
        loop = asyncio.new_event_loop()
        try:
            pool = install_prewarmed_default_executor(loop, workers=8)
            while len(pool._threads) < 8:  # prewarm is asynchronous by design
                time.sleep(0.01)

            grew: list[int] = []
            original = pool._adjust_thread_count

            def watched() -> None:
                before = len(pool._threads)
                original()
                if len(pool._threads) != before:
                    grew.append(len(pool._threads))

            pool._adjust_thread_count = watched

            async def hammer() -> None:
                await asyncio.gather(*(asyncio.to_thread(lambda: None) for _ in range(40)))

            loop.run_until_complete(hammer())
            assert grew == [], "the pool grew under the loop"
        finally:
            loop.close()

    def test_the_loop_uses_the_installed_pool(self):
        loop = asyncio.new_event_loop()
        try:
            pool = install_prewarmed_default_executor(loop, workers=4)
            name = loop.run_until_complete(
                asyncio.to_thread(lambda: threading.current_thread().name)
            )
            assert name.startswith("jarvis-io")
            pool.shutdown(wait=False)
        finally:
            loop.close()
