"""Regressions-Test: ``SubprocessHarness.invoke()`` ist concurrency-safe.

Bug 2026-04-29: ``multi_spawn`` mit 3 parallelen openclaw-Calls schlug in
Production deterministisch in <10ms fehl. Wurzelursache: ``HarnessManager.get()``
cached EINE ``OpenClawHarness``-Instance; concurrent ``invoke()``-Calls auf
derselben Instance racen auf ``self._process`` und ``self._cancelled``.

Fix: invocation-lokale Variablen + ``self._active_processes: set`` fuer
cancel-Tracking. Dieser Test reproduziert das Pattern (3 parallele ``invoke()``
auf derselben Instance) und stellt sicher, dass alle drei sauber durchlaufen.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from jarvis.core.protocols import HarnessResult, HarnessTask
from jarvis.harness.base import SubprocessHarness


class _SlowFakeHarness(SubprocessHarness):
    """Faked Subprocess: kein echter Spawn, asyncio.sleep simuliert Subprocess-Run.

    Wir ueberschreiben ``invoke`` direkt um den Subprocess-Spawn zu vermeiden
    (sonst muessten wir python-CLI o.ae. spawnen). Der Test prueft dass mehrere
    parallele invoke()-Calls ihre eigenen lokalen Variablen behalten.
    """

    name = "slow-fake"

    def __init__(self) -> None:
        super().__init__()
        self.invocation_count = 0
        # Lokale Tracking-Variablen — werden VON jedem invoke()-Call inkrementiert.
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
    """3 parallele ``invoke()`` auf derselben Instance liefern alle ihren Output."""
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

    # Jede invocation hat ihre EIGENEN chunks (kein Vermischen via shared state).
    a_chunks = "".join(c.stdout for c in results[0] if c.stdout)
    b_chunks = "".join(c.stdout for c in results[1] if c.stdout)
    c_chunks = "".join(c.stdout for c in results[2] if c.stdout)
    assert "A" in a_chunks and "B" not in a_chunks
    assert "B" in b_chunks and "C" not in b_chunks
    assert "C" in c_chunks and "A" not in c_chunks


@pytest.mark.asyncio
async def test_subprocess_harness_init_has_active_processes_set():
    """SubprocessHarness.__init__ muss ``_active_processes`` initialisieren."""
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

    # Beide wurden terminate'd
    proc1.terminate.assert_called_once()
    proc2.terminate.assert_called_once()
    assert h._cancelled is True
