"""Global desktop actuation lock at the ComputerUseHarness boundary
(deep-dive 2026-07-15, H-10).

There is ONE physical mouse/keyboard: two missions with DIFFERENT goals used
to run concurrently and race each other's pointer moves and foreground
guards (the tool layer only dedupes IDENTICAL goals). The harness now
serializes missions; a queued mission honors its own deadline and
cancellation while waiting.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator

import pytest

from jarvis.control import KillSwitch
from jarvis.core.bus import EventBus
from jarvis.core.protocols import HarnessResult, HarnessTask
from jarvis.harness.computer_use_context import ComputerUseContext
from jarvis.plugins.harness import computer_use as cu_mod
from jarvis.plugins.harness.computer_use import ComputerUseHarness


def _ctx(bus: EventBus) -> ComputerUseContext:
    return ComputerUseContext(
        vision_engine=object(),
        brain_manager=object(),
        tool_executor=object(),
        bus=bus,
        kill_switch=KillSwitch(),
    )


@pytest.fixture(autouse=True)
def _fresh_lock_and_registry():
    """Fresh module lock per test + clean CU token registry/suppression."""
    from jarvis.harness.computer_use_context import (
        clear_cu_suppression,
        register_active_cu_token,
    )

    cu_mod._DESKTOP_LOCK = asyncio.Lock()
    clear_cu_suppression()
    register_active_cu_token(None)
    yield
    cu_mod._DESKTOP_LOCK = asyncio.Lock()
    clear_cu_suppression()
    register_active_cu_token(None)


async def test_two_missions_never_overlap(monkeypatch: pytest.MonkeyPatch) -> None:
    """The actuation windows of two concurrent missions must be disjoint."""
    windows: list[tuple[float, float]] = []

    def loop_factory(task: HarnessTask, ctx, cancel_token=None) -> AsyncIterator[HarnessResult]:
        async def run() -> AsyncIterator[HarnessResult]:
            start = time.monotonic()
            await asyncio.sleep(0.15)  # simulated desktop activity
            windows.append((start, time.monotonic()))
            yield HarnessResult(stdout="ok", exit_code=0, is_final=True)
        return run()

    monkeypatch.setattr(cu_mod, "_resolve_run_cu_loop", lambda: loop_factory)
    bus = EventBus()
    ctx = _ctx(bus)
    a = ComputerUseHarness(context=ctx)
    b = ComputerUseHarness(context=ctx)

    async def drain(h: ComputerUseHarness, goal: str) -> None:
        async for _ in h.invoke(HarnessTask(prompt=goal, timeout_s=10)):
            pass

    await asyncio.gather(drain(a, "goal a"), drain(b, "goal b"))

    assert len(windows) == 2
    (s1, e1), (s2, e2) = sorted(windows)
    assert e1 <= s2, "second mission actuated while the first was still active"


async def test_queued_mission_cancel_aborts_without_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancelling a QUEUED mission must end it (exit 130) before its loop
    ever runs — the wait itself is cancellable."""
    ran: list[str] = []
    release = asyncio.Event()

    def loop_factory(task: HarnessTask, ctx, cancel_token=None) -> AsyncIterator[HarnessResult]:
        async def run() -> AsyncIterator[HarnessResult]:
            ran.append(task.prompt)
            await release.wait()
            yield HarnessResult(stdout="ok", exit_code=0, is_final=True)
        return run()

    monkeypatch.setattr(cu_mod, "_resolve_run_cu_loop", lambda: loop_factory)
    bus = EventBus()
    ctx = _ctx(bus)
    first = ComputerUseHarness(context=ctx)
    queued = ComputerUseHarness(context=ctx)

    async def drain(h: ComputerUseHarness, goal: str) -> list[HarnessResult]:
        return [c async for c in h.invoke(HarnessTask(prompt=goal, timeout_s=10))]

    first_task = asyncio.create_task(drain(first, "holder"))
    await asyncio.sleep(0.05)  # first mission holds the desktop lock
    queued_task = asyncio.create_task(drain(queued, "queued"))
    await asyncio.sleep(0.05)  # queued mission is now waiting on the lock

    await queued.cancel()
    chunks = await asyncio.wait_for(queued_task, timeout=2.0)
    assert chunks and chunks[-1].exit_code == 130
    assert ran == ["holder"]  # the queued loop never started

    release.set()
    await asyncio.wait_for(first_task, timeout=2.0)


async def test_queued_mission_times_out_honestly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A queued mission whose deadline expires while waiting reports the
    timeout (exit 124) instead of hanging forever."""
    release = asyncio.Event()

    def loop_factory(task: HarnessTask, ctx, cancel_token=None) -> AsyncIterator[HarnessResult]:
        async def run() -> AsyncIterator[HarnessResult]:
            await release.wait()
            yield HarnessResult(stdout="ok", exit_code=0, is_final=True)
        return run()

    monkeypatch.setattr(cu_mod, "_resolve_run_cu_loop", lambda: loop_factory)
    bus = EventBus()
    ctx = _ctx(bus)
    first = ComputerUseHarness(context=ctx)
    queued = ComputerUseHarness(context=ctx)

    async def drain(h: ComputerUseHarness, goal: str, timeout_s: float) -> list[HarnessResult]:
        return [c async for c in h.invoke(HarnessTask(prompt=goal, timeout_s=timeout_s))]

    first_task = asyncio.create_task(drain(first, "holder", 10))
    await asyncio.sleep(0.05)
    chunks = await asyncio.wait_for(
        asyncio.create_task(drain(queued, "queued", 0.3)), timeout=3.0,
    )
    assert chunks and chunks[-1].exit_code == 124
    assert "waiting for the desktop" in (chunks[-1].stderr or "")

    release.set()
    await asyncio.wait_for(first_task, timeout=2.0)
