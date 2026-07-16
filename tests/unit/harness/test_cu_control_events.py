"""CUControlStarted/Ended contract at the ComputerUseHarness boundary.

The screen indicator (jarvis/cu/indicator) refcounts exactly these events,
so the pair must fire once per mission on EVERY exit path — normal finish,
cancel (exit 130), non-zero error exit, and harness timeout.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from jarvis.control import KillSwitch
from jarvis.core.bus import EventBus
from jarvis.core.events import CUControlEnded, CUControlStarted
from jarvis.core.protocols import HarnessResult, HarnessTask
from jarvis.harness.computer_use_context import ComputerUseContext
from jarvis.plugins.harness import computer_use as cu_mod
from jarvis.plugins.harness.computer_use import ComputerUseHarness


class _Recorder:
    def __init__(self, bus: EventBus) -> None:
        self.started: list[CUControlStarted] = []
        self.ended: list[CUControlEnded] = []
        bus.subscribe(CUControlStarted, self._on_started)
        bus.subscribe(CUControlEnded, self._on_ended)

    async def _on_started(self, ev: CUControlStarted) -> None:
        self.started.append(ev)

    async def _on_ended(self, ev: CUControlEnded) -> None:
        self.ended.append(ev)


def _ctx(bus: EventBus) -> ComputerUseContext:
    return ComputerUseContext(
        vision_engine=object(),
        brain_manager=object(),
        tool_executor=object(),
        bus=bus,
        kill_switch=KillSwitch(),
    )


def _stub_loop(*chunks: HarnessResult, hang: bool = False):
    def factory(task: HarnessTask, ctx, cancel_token=None) -> AsyncIterator[HarnessResult]:
        async def run() -> AsyncIterator[HarnessResult]:
            for chunk in chunks:
                yield chunk
            if hang:
                await asyncio.sleep(3600)
        return run()

    return factory


async def _invoke_all(harness: ComputerUseHarness, task: HarnessTask) -> list[HarnessResult]:
    return [chunk async for chunk in harness.invoke(task)]


@pytest.fixture(autouse=True)
def _clean_cu_registry():
    """Isolate from other tests: no stale suppression window / tokens."""
    from jarvis.harness.computer_use_context import (
        clear_cu_suppression,
        register_active_cu_token,
    )

    clear_cu_suppression()
    register_active_cu_token(None)  # clears the registry (test-only path)
    yield
    clear_cu_suppression()
    register_active_cu_token(None)


@pytest.fixture()
def bus() -> EventBus:
    return EventBus()


async def test_started_and_ended_fire_once_on_success(
    bus: EventBus, monkeypatch: pytest.MonkeyPatch
) -> None:
    rec = _Recorder(bus)
    monkeypatch.setattr(
        cu_mod,
        "_resolve_run_cu_loop",
        lambda: _stub_loop(HarnessResult(stdout="done", exit_code=0, is_final=True)),
    )
    harness = ComputerUseHarness(context=_ctx(bus))
    await _invoke_all(harness, HarnessTask(prompt="click something", timeout_s=5))

    assert len(rec.started) == 1
    assert len(rec.ended) == 1
    assert rec.ended[0].reason == "finished"
    assert rec.started[0].mission_id == rec.ended[0].mission_id
    assert rec.started[0].mission_id != ""


async def test_cancel_exit_code_reports_cancelled(
    bus: EventBus, monkeypatch: pytest.MonkeyPatch
) -> None:
    rec = _Recorder(bus)
    monkeypatch.setattr(
        cu_mod,
        "_resolve_run_cu_loop",
        lambda: _stub_loop(
            HarnessResult(stdout="", exit_code=130, is_final=True)
        ),
    )
    harness = ComputerUseHarness(context=_ctx(bus))
    await _invoke_all(harness, HarnessTask(prompt="x", timeout_s=5))
    assert [ev.reason for ev in rec.ended] == ["cancelled"]


async def test_error_exit_code_reports_error(
    bus: EventBus, monkeypatch: pytest.MonkeyPatch
) -> None:
    rec = _Recorder(bus)
    monkeypatch.setattr(
        cu_mod,
        "_resolve_run_cu_loop",
        lambda: _stub_loop(HarnessResult(stdout="", exit_code=7, is_final=True)),
    )
    harness = ComputerUseHarness(context=_ctx(bus))
    await _invoke_all(harness, HarnessTask(prompt="x", timeout_s=5))
    assert [ev.reason for ev in rec.ended] == ["error"]


async def test_timeout_reports_timeout_and_still_ends(
    bus: EventBus, monkeypatch: pytest.MonkeyPatch
) -> None:
    rec = _Recorder(bus)
    monkeypatch.setattr(cu_mod, "_resolve_run_cu_loop", lambda: _stub_loop(hang=True))
    harness = ComputerUseHarness(context=_ctx(bus))
    chunks = await _invoke_all(
        harness, HarnessTask(prompt="x", timeout_s=1)
    )
    assert chunks and chunks[-1].exit_code == 124
    assert len(rec.started) == 1
    assert [ev.reason for ev in rec.ended] == ["timeout"]


async def test_abandoned_stream_still_publishes_ended(
    bus: EventBus, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A consumer that aborts the invoke stream mid-mission (break /
    aclose / GC of the outer generator) must still get CUControlEnded —
    otherwise the yellow border stays lit and Escape stays armed."""
    rec = _Recorder(bus)
    monkeypatch.setattr(
        cu_mod,
        "_resolve_run_cu_loop",
        lambda: _stub_loop(
            HarnessResult(stdout="progress", exit_code=0, is_final=False),
            HarnessResult(stdout="done", exit_code=0, is_final=True),
        ),
    )
    harness = ComputerUseHarness(context=_ctx(bus))
    stream = harness.invoke(HarnessTask(prompt="x", timeout_s=5))
    first = await anext(stream)
    assert first.is_final is False
    await stream.aclose()  # consumer walks away mid-mission

    assert len(rec.started) == 1
    assert len(rec.ended) == 1, (
        "CUControlEnded must fire deterministically on an abandoned stream"
    )
    assert rec.started[0].mission_id == rec.ended[0].mission_id


async def test_manager_dispatch_closes_harness_stream_on_break(
    bus: EventBus, monkeypatch: pytest.MonkeyPatch
) -> None:
    """HarnessManager.dispatch must aclose() the inner harness stream when
    its own consumer breaks early, so the CU finally never waits on GC."""
    from jarvis.harness.manager import HarnessManager

    rec = _Recorder(bus)
    monkeypatch.setattr(
        cu_mod,
        "_resolve_run_cu_loop",
        lambda: _stub_loop(
            HarnessResult(stdout="progress", exit_code=0, is_final=False),
            HarnessResult(stdout="done", exit_code=0, is_final=True),
        ),
    )
    manager = HarnessManager(bus=bus)
    # Seed the instance directly — entry-point discovery would build the
    # real harness against the global (unset) context.
    manager._loaded = True
    manager._classes["screenshot"] = ComputerUseHarness
    manager._instances["screenshot"] = ComputerUseHarness(context=_ctx(bus))
    stream = manager.dispatch("screenshot", HarnessTask(prompt="x", timeout_s=5))
    async for _result in stream:
        break  # abandon after the first chunk
    await stream.aclose()

    assert len(rec.started) == 1
    assert len(rec.ended) == 1


async def test_concurrent_missions_pair_up(
    bus: EventBus, monkeypatch: pytest.MonkeyPatch
) -> None:
    rec = _Recorder(bus)
    monkeypatch.setattr(
        cu_mod,
        "_resolve_run_cu_loop",
        lambda: _stub_loop(HarnessResult(stdout="ok", exit_code=0, is_final=True)),
    )
    ctx = _ctx(bus)
    a = ComputerUseHarness(context=ctx)
    b = ComputerUseHarness(context=ctx)
    await asyncio.gather(
        _invoke_all(a, HarnessTask(prompt="a", timeout_s=5)),
        _invoke_all(b, HarnessTask(prompt="b", timeout_s=5)),
    )
    assert len(rec.started) == 2
    assert len(rec.ended) == 2
    assert {e.mission_id for e in rec.started} == {e.mission_id for e in rec.ended}
    assert len({e.mission_id for e in rec.started}) == 2
