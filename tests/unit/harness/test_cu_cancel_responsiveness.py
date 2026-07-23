"""Esc / voice-hangup / Emergency-Stop must abort a RUNNING Computer-Use step
immediately, not only at the loop's next checkpoint.

Live 2026-07-23: the yellow screen indicator's "Esc to cancel" fired
(``[cu-indicator] Escape pressed — cancelled``) but the mission kept going —
the user pressed Esc five times over 3 s and the run continued ~9 s more. Root
cause: the harness drove the loop with a single ``await anext(stream)`` per
step, and the think-phase brain call is ONE long uninterruptible await (15-68 s
live). The cancel token was only read at the loop's own ``is_cancelled()``
checkpoints, which sit BETWEEN steps — so a cancel during the brain call did
nothing until that call returned. The consumer now polls the token on a short
heartbeat and cancels the in-flight step task the instant the flag is set.

The heartbeat POLLS the flag rather than awaiting a cancel event on purpose:
on the py3.11 Windows proactor loop an event wakeup can be absorbed when no
timer is armed (BUG-081), which would strand a running mission after Esc.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator

import pytest

from jarvis.control import KillSwitch
from jarvis.core.bus import EventBus
from jarvis.core.protocols import HarnessResult, HarnessTask
from jarvis.harness.computer_use_context import (
    ComputerUseContext,
    cancel_active_cu,
)
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


async def test_escape_aborts_a_running_step_immediately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cancel during a long in-flight step ends the mission (exit 130) within
    a couple of heartbeats — it must NOT wait for the step to finish."""
    step_started = asyncio.Event()
    release = asyncio.Event()  # NEVER set: stands in for a 15-68 s brain call

    def loop_factory(task: HarnessTask, ctx, cancel_token=None) -> AsyncIterator[HarnessResult]:
        async def run() -> AsyncIterator[HarnessResult]:
            yield HarnessResult(stdout="[cu] step 1: thinking\n", is_final=False)
            step_started.set()
            await release.wait()  # the long, checkpoint-free await
            yield HarnessResult(stdout="done", exit_code=0, is_final=True)
        return run()

    monkeypatch.setattr(cu_mod, "_resolve_run_cu_loop", lambda: loop_factory)
    harness = ComputerUseHarness(context=_ctx(EventBus()))

    async def drain() -> list[HarnessResult]:
        return [c async for c in harness.invoke(HarnessTask(prompt="do it", timeout_s=30))]

    task = asyncio.create_task(drain())
    await asyncio.wait_for(step_started.wait(), timeout=2.0)
    t0 = time.monotonic()
    cancel_active_cu("user_escape", suppress_new=False)  # exactly what Esc does
    chunks = await asyncio.wait_for(task, timeout=2.0)
    elapsed = time.monotonic() - t0

    assert chunks and chunks[-1].exit_code == 130
    assert not release.is_set(), "the test never released the step"
    assert elapsed < 1.0, f"cancel took {elapsed:.2f}s — not immediate"


async def test_cancel_before_first_yield_is_immediate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The very first think can run BEFORE any progress chunk. A cancel while
    that first step blocks must still abort at once (not after it returns)."""
    entered = asyncio.Event()
    release = asyncio.Event()  # never set

    def loop_factory(task: HarnessTask, ctx, cancel_token=None) -> AsyncIterator[HarnessResult]:
        async def run() -> AsyncIterator[HarnessResult]:
            entered.set()
            await release.wait()  # blocks before ANY yield
            yield HarnessResult(stdout="done", exit_code=0, is_final=True)
        return run()

    monkeypatch.setattr(cu_mod, "_resolve_run_cu_loop", lambda: loop_factory)
    harness = ComputerUseHarness(context=_ctx(EventBus()))

    async def drain() -> list[HarnessResult]:
        return [c async for c in harness.invoke(HarnessTask(prompt="do it", timeout_s=30))]

    task = asyncio.create_task(drain())
    await asyncio.wait_for(entered.wait(), timeout=2.0)
    t0 = time.monotonic()
    cancel_active_cu("user_escape", suppress_new=False)
    chunks = await asyncio.wait_for(task, timeout=2.0)

    assert chunks and chunks[-1].exit_code == 130
    assert time.monotonic() - t0 < 1.0


async def test_uncancelled_mission_still_completes_normally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The heartbeat consumer must not disturb a normal run: every chunk still
    arrives and the final exit code is preserved."""
    def loop_factory(task: HarnessTask, ctx, cancel_token=None) -> AsyncIterator[HarnessResult]:
        async def run() -> AsyncIterator[HarnessResult]:
            yield HarnessResult(stdout="[cu] step 1\n", is_final=False)
            await asyncio.sleep(0.05)
            yield HarnessResult(stdout="[cu] done\n", exit_code=0, is_final=True)
        return run()

    monkeypatch.setattr(cu_mod, "_resolve_run_cu_loop", lambda: loop_factory)
    harness = ComputerUseHarness(context=_ctx(EventBus()))

    chunks = [
        c async for c in harness.invoke(HarnessTask(prompt="do it", timeout_s=10))
    ]

    assert [c.is_final for c in chunks] == [False, True]
    assert chunks[-1].exit_code == 0
    assert chunks[-1].stdout == "[cu] done\n"
