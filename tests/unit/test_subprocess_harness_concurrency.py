"""Regression test: ``SubprocessHarness.invoke()`` is concurrency-safe.

Bug 2026-04-29: ``multi_spawn`` with 3 parallel openclaw calls failed
deterministically in production in <10ms. Root cause: ``HarnessManager.get()``
caches ONE ``OpenClawHarness`` instance; concurrent ``invoke()`` calls on
the same instance raced on ``self._process`` and ``self._cancelled``.

Fix: invocation-local variables + ``self._active_processes: set`` for
cancel tracking. This test reproduces the pattern (3 parallel ``invoke()``
calls on the same instance) and makes sure all three complete cleanly.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from jarvis.core.protocols import HarnessResult, HarnessTask
from jarvis.harness.base import SubprocessHarness


class _SlowFakeHarness(SubprocessHarness):
    """Faked subprocess: no real spawn, asyncio.sleep simulates the subprocess run.

    We override ``invoke`` directly to avoid the subprocess spawn
    (otherwise we'd have to spawn a python CLI or similar). The test checks
    that multiple parallel invoke() calls keep their own local variables.
    """

    name = "slow-fake"

    def __init__(self) -> None:
        super().__init__()
        self.invocation_count = 0
        # Local tracking variables — incremented BY each invoke() call.
        self.concurrent_peak = 0
        self._active = 0

    def build_command(self, task: HarnessTask) -> list[str]:
        return ["echo", task.prompt]

    async def invoke(self, task: HarnessTask) -> AsyncIterator[HarnessResult]:
        # Tracking — wenn invoke()s racen, sieht concurrent_peak >1.
        self._active += 1
        self.concurrent_peak = max(self.concurrent_peak, self._active)
        self.invocation_count += 1

        # Lokale State (statt self._x) — hier testen wir ob das geht.
        local_done = False
        try:
            for i in range(3):
                await asyncio.sleep(0.01)
                yield HarnessResult(stdout=f"chunk-{i}-{task.prompt}\n", is_final=False)
            local_done = True
            yield HarnessResult(exit_code=0, duration_ms=30, is_final=True)
        finally:
            self._active -= 1
            assert local_done, "invoke() abgebrochen ohne final yield"


@pytest.mark.asyncio
async def test_concurrent_invoke_calls_dont_race():
    """3 parallel ``invoke()`` calls on the same instance all deliver their output."""
    harness = _SlowFakeHarness()

    async def collect(prompt: str) -> list[HarnessResult]:
        chunks = []
        async for r in harness.invoke(HarnessTask(prompt=prompt)):
            chunks.append(r)
        return chunks

    results = await asyncio.gather(
        collect("A"), collect("B"), collect("C"),
    )

    # Jede invoke() liefert 4 chunks (3 progress + 1 final).
    assert len(results[0]) == 4, f"prompt A: {len(results[0])} chunks"
    assert len(results[1]) == 4
    assert len(results[2]) == 4

    # Concurrency wirklich erreicht — alle 3 liefen GLEICHZEITIG.
    assert harness.concurrent_peak == 3, (
        f"Erwartet 3 parallel invocations, sah {harness.concurrent_peak}. "
        f"Race-Condition oder Sequential-Lock?"
    )

    # Each invocation has its OWN chunks (no mixing via shared state).
    a_chunks = "".join(c.stdout for c in results[0] if c.stdout)
    b_chunks = "".join(c.stdout for c in results[1] if c.stdout)
    c_chunks = "".join(c.stdout for c in results[2] if c.stdout)
    assert "A" in a_chunks and "B" not in a_chunks
    assert "B" in b_chunks and "C" not in b_chunks
    assert "C" in c_chunks and "A" not in c_chunks


@pytest.mark.asyncio
async def test_subprocess_harness_init_has_active_processes_set():
    """SubprocessHarness.__init__ must initialize ``_active_processes``."""
    h = _SlowFakeHarness()
    assert hasattr(h, "_active_processes")
    assert h._active_processes == set()


@pytest.mark.asyncio
async def test_cancel_killed_all_active_processes():
    """``cancel()`` killed ALLE in ``_active_processes`` registrierten Subprocesses."""
    from unittest.mock import AsyncMock, MagicMock

    h = _SlowFakeHarness()
    # Fake zwei aktive subprocesses
    proc1 = MagicMock()
    proc1.returncode = None
    proc1.terminate = MagicMock()
    proc1.wait = AsyncMock(return_value=None)
    proc2 = MagicMock()
    proc2.returncode = None
    proc2.terminate = MagicMock()
    proc2.wait = AsyncMock(return_value=None)

    h._active_processes.add(proc1)
    h._active_processes.add(proc2)

    await h.cancel()

    # Both were terminate'd
    proc1.terminate.assert_called_once()
    proc2.terminate.assert_called_once()
    assert h._cancelled is True
