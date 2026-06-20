"""Per-loader timing for Phase A warm-up.

The Phase A `asyncio.gather` hides which loader dominates, so a 28–52 s warm-up
gave no signal about WHERE the time went. ``_gather_timed`` records each named
loader's wall-clock so the boot can log per-loader ms (and tests/diagnostics can
read it), even when a loader fails.
"""
from __future__ import annotations

import asyncio

from jarvis.speech.pipeline import _gather_timed


async def test_gather_timed_records_each_task_duration() -> None:
    async def fast() -> str:
        return "f"

    async def slow() -> str:
        await asyncio.sleep(0.05)
        return "s"

    timings, results = await _gather_timed([("fast", fast), ("slow", slow)])

    assert set(timings) == {"fast", "slow"}
    assert timings["slow"] >= timings["fast"]
    assert results == ["f", "s"]  # order preserved


async def test_gather_timed_records_duration_even_on_failure() -> None:
    async def ok() -> int:
        return 1

    async def boom() -> None:
        raise ValueError("x")

    timings, results = await _gather_timed([("ok", ok), ("boom", boom)])

    # Timing is captured for the failing task too (recorded in finally).
    assert set(timings) == {"ok", "boom"}
    assert results[0] == 1
    assert isinstance(results[1], Exception)  # gather(return_exceptions=True)
