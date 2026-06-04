"""Regression tests for the brain *no-progress* (stall) timeout.

Live bug 2026-06-01: a voice vision question ("Was ist das hier?") triggers a
Gemini tool-use loop (large image upload + context-cache build + function_call
+ tool execution). The whole turn legitimately exceeds the old 25 s TOTAL
wall-clock cap, so ``asyncio.wait_for`` cancelled the in-flight turn mid-work
and spoke "That took too long, say it again" — Jarvis looked lazy while it was
actually still working.

Root cause: a single wall-clock cap cannot tell a genuinely STALLED provider
(no progress, ever) apart from a slow-but-working one (steady tool/token
progress). The fix replaces the total cap with a deadline that *resets on every
progress signal* (``_mark_brain_progress``), with an absolute ceiling as the
pathological-drip-feed backstop.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from jarvis.speech.pipeline import SpeechPipeline


def _make_pipeline(*, stall: float, ceiling: float, poll: float) -> SpeechPipeline:
    """A bare pipeline with only the stall-guard attributes wired.

    Mirrors the ctor-bypass pattern used across tests/unit/speech (the full
    SpeechPipeline ctor needs audio devices we don't have in unit scope).
    """
    p = SpeechPipeline.__new__(SpeechPipeline)
    p._brain_timeout_s = stall
    p._brain_hard_timeout_s = ceiling
    p._brain_stall_poll_s = poll
    p._brain_last_progress = time.monotonic()
    return p


@pytest.mark.asyncio
async def test_stall_guard_times_out_when_brain_makes_no_progress() -> None:
    """A genuinely stalled brain (never calls progress) raises TimeoutError —
    the original liveness guard against a hung provider is preserved."""
    p = _make_pipeline(stall=0.3, ceiling=5.0, poll=0.05)

    async def never_progresses() -> tuple[str, bool]:
        await asyncio.sleep(10.0)
        return ("unreachable", False)

    with pytest.raises(TimeoutError):
        await p._run_brain_with_stall_guard(never_progresses())


@pytest.mark.asyncio
async def test_stall_guard_completes_a_slow_but_working_turn() -> None:
    """THE FIX: a turn whose total runtime far exceeds the stall window — but
    whose individual no-progress gaps stay *under* it — runs to completion and
    delivers its result, instead of being guillotined like the old total cap."""
    p = _make_pipeline(stall=0.3, ceiling=10.0, poll=0.05)

    async def slow_but_working() -> tuple[str, bool]:
        # 6 x 0.15 s = 0.9 s total, well past the 0.3 s stall window, but every
        # gap (0.15 s) is below it — exactly the vision+tool-loop profile.
        for _ in range(6):
            await asyncio.sleep(0.15)
            p._mark_brain_progress()
        return ("Das ist dein Editor.", False)

    result = await p._run_brain_with_stall_guard(slow_but_working())

    assert result == ("Das ist dein Editor.", False)


@pytest.mark.asyncio
async def test_stall_guard_enforces_the_absolute_ceiling() -> None:
    """Even a brain that keeps pinging progress forever (pathological drip
    feed) is bounded by the hard ceiling so the session can never wedge."""
    p = _make_pipeline(stall=5.0, ceiling=0.4, poll=0.05)

    async def progresses_forever() -> tuple[str, bool]:
        while True:
            await asyncio.sleep(0.05)
            p._mark_brain_progress()

    with pytest.raises(TimeoutError):
        await p._run_brain_with_stall_guard(progresses_forever())


@pytest.mark.asyncio
async def test_stall_guard_propagates_a_brain_error() -> None:
    """A brain coroutine that raises surfaces its exception unchanged — the
    caller's ``except Exception`` branch must keep handling provider failures."""
    p = _make_pipeline(stall=1.0, ceiling=5.0, poll=0.05)

    async def boom() -> tuple[str, bool]:
        raise RuntimeError("provider exploded")

    with pytest.raises(RuntimeError, match="provider exploded"):
        await p._run_brain_with_stall_guard(boom())


@pytest.mark.asyncio
async def test_stall_guard_cancels_the_brain_task_on_timeout() -> None:
    """On a stall the in-flight brain coroutine must actually be cancelled —
    no orphaned task keeps running (and possibly speaking) after we gave up."""
    p = _make_pipeline(stall=0.2, ceiling=5.0, poll=0.05)
    cancelled = asyncio.Event()

    async def runs_until_cancelled() -> tuple[str, bool]:
        try:
            await asyncio.sleep(10.0)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return ("unreachable", False)

    with pytest.raises(TimeoutError):
        await p._run_brain_with_stall_guard(runs_until_cancelled())

    assert cancelled.is_set()
