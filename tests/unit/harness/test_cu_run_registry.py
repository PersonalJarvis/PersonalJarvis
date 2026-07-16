"""CU run registry + its harness integration (deep-dive 2026-07-15, H-09).

The run-control REST surface needs an inventory of missions with a working
per-id cancel. The harness records every mission regardless of launch route;
these tests pin the lifecycle (queued -> running -> terminal), the cancel
semantics, the boundedness, and the id pass-through from task.env.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from jarvis.control import CancelToken, KillSwitch
from jarvis.core.bus import EventBus
from jarvis.core.protocols import HarnessResult, HarnessTask
from jarvis.harness import cu_run_registry as reg
from jarvis.harness.computer_use_context import ComputerUseContext
from jarvis.plugins.harness import computer_use as cu_mod
from jarvis.plugins.harness.computer_use import (
    CU_MISSION_ID_ENV_KEY,
    CU_SOURCE_ENV_KEY,
    ComputerUseHarness,
)


@pytest.fixture(autouse=True)
def _clean_state():
    from jarvis.harness.computer_use_context import (
        clear_cu_suppression,
        register_active_cu_token,
    )

    reg.clear_runs()
    cu_mod._DESKTOP_LOCK = asyncio.Lock()
    clear_cu_suppression()
    register_active_cu_token(None)
    yield
    reg.clear_runs()
    cu_mod._DESKTOP_LOCK = asyncio.Lock()
    clear_cu_suppression()
    register_active_cu_token(None)


# ----------------------------------------------------------------------
# Pure registry semantics
# ----------------------------------------------------------------------

def test_lifecycle_and_snapshot_shape() -> None:
    token = CancelToken()
    reg.register_run("m1", "open the browser", token, source="api")
    run = reg.get_run("m1")
    assert run is not None
    assert run["status"] == "queued"
    assert run["goal"] == "open the browser"
    assert run["source"] == "api"
    assert "token" not in run  # never expose the token

    reg.mark_running("m1")
    assert reg.get_run("m1")["status"] == "running"
    assert reg.active_run_count() == 1

    reg.finish_run("m1", "finished", exit_code=0, result_text="proof text")
    run = reg.get_run("m1")
    assert run["status"] == "finished"
    assert run["exit_code"] == 0
    assert run["result_text"] == "proof text"
    assert run["ended_at"] is not None
    assert reg.active_run_count() == 0


def test_cancel_fires_the_token_only_while_active() -> None:
    token = CancelToken()
    reg.register_run("m1", "goal", token)
    assert reg.cancel_run("m1") is True
    assert token.is_cancelled()
    assert token.reason == "api_cancel"

    # Terminal runs and unknown ids refuse.
    reg.finish_run("m1", "cancelled", exit_code=130)
    assert reg.cancel_run("m1") is False
    assert reg.cancel_run("nope") is False


def test_cancel_all_hits_every_active_run() -> None:
    tokens = [CancelToken() for _ in range(3)]
    for i, tok in enumerate(tokens):
        reg.register_run(f"m{i}", f"goal {i}", tok)
    reg.finish_run("m0", "finished", exit_code=0)  # terminal: not cancelled

    assert reg.cancel_all_runs() == 2
    assert not tokens[0].is_cancelled()
    assert tokens[1].is_cancelled()
    assert tokens[2].is_cancelled()


def test_registry_stays_bounded() -> None:
    for i in range(reg._MAX_RUNS + 30):
        reg.register_run(f"m{i}", "g", CancelToken())
        reg.finish_run(f"m{i}", "finished", exit_code=0)
    assert len(reg.list_runs(limit=1000)) <= reg._MAX_RUNS


def test_list_runs_newest_first() -> None:
    reg.register_run("old", "g1", CancelToken())
    reg._RUNS["old"].started_at -= 100
    reg.register_run("new", "g2", CancelToken())
    ids = [r["mission_id"] for r in reg.list_runs()]
    assert ids == ["new", "old"]


# ----------------------------------------------------------------------
# Harness integration
# ----------------------------------------------------------------------

def _ctx(bus: EventBus) -> ComputerUseContext:
    return ComputerUseContext(
        vision_engine=object(),
        brain_manager=object(),
        tool_executor=object(),
        bus=bus,
        kill_switch=KillSwitch(),
    )


def _stub_loop(*chunks: HarnessResult):
    def factory(task: HarnessTask, ctx, cancel_token=None) -> AsyncIterator[HarnessResult]:
        async def run() -> AsyncIterator[HarnessResult]:
            for chunk in chunks:
                yield chunk
        return run()

    return factory


async def test_harness_records_the_run_with_env_provided_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cu_mod,
        "_resolve_run_cu_loop",
        lambda: _stub_loop(
            HarnessResult(stdout="the proof", exit_code=0, is_final=True)
        ),
    )
    harness = ComputerUseHarness(context=_ctx(EventBus()))
    task = HarnessTask(
        prompt="open the browser",
        timeout_s=5,
        env={CU_MISSION_ID_ENV_KEY: "rest123", CU_SOURCE_ENV_KEY: "api"},
    )
    async for _ in harness.invoke(task):
        pass

    run = reg.get_run("rest123")
    assert run is not None
    assert run["status"] == "finished"
    assert run["source"] == "api"
    assert run["exit_code"] == 0
    assert run["result_text"] == "the proof"


async def test_harness_records_error_and_timeout_statuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cu_mod,
        "_resolve_run_cu_loop",
        lambda: _stub_loop(HarnessResult(stdout="", exit_code=7, is_final=True)),
    )
    harness = ComputerUseHarness(context=_ctx(EventBus()))
    async for _ in harness.invoke(
        HarnessTask(prompt="x", timeout_s=5, env={CU_MISSION_ID_ENV_KEY: "err1"})
    ):
        pass
    assert reg.get_run("err1")["status"] == "error"

    def hanging_factory(task, ctx, cancel_token=None):
        async def run() -> AsyncIterator[HarnessResult]:
            await asyncio.sleep(3600)
            if False:  # pragma: no cover
                yield None
        return run()

    monkeypatch.setattr(cu_mod, "_resolve_run_cu_loop", lambda: hanging_factory)
    async for _ in harness.invoke(
        HarnessTask(prompt="x", timeout_s=1, env={CU_MISSION_ID_ENV_KEY: "to1"})
    ):
        pass
    run = reg.get_run("to1")
    assert run["status"] == "timeout"
    assert run["exit_code"] == 124


async def test_registry_cancel_stops_a_live_mission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The full H-09 cancel chain: registry cancel -> token -> loop stops."""
    started = asyncio.Event()

    def factory(task: HarnessTask, ctx, cancel_token=None):
        async def run() -> AsyncIterator[HarnessResult]:
            started.set()
            await cancel_token.wait_until_cancelled()
            yield HarnessResult(stdout="", exit_code=130, is_final=True)
        return run()

    monkeypatch.setattr(cu_mod, "_resolve_run_cu_loop", lambda: factory)
    harness = ComputerUseHarness(context=_ctx(EventBus()))

    async def drain() -> list[HarnessResult]:
        return [
            c async for c in harness.invoke(
                HarnessTask(
                    prompt="live", timeout_s=10,
                    env={CU_MISSION_ID_ENV_KEY: "live1"},
                )
            )
        ]

    run_task = asyncio.create_task(drain())
    await asyncio.wait_for(started.wait(), timeout=2.0)
    assert reg.get_run("live1")["status"] == "running"

    assert reg.cancel_run("live1") is True
    chunks = await asyncio.wait_for(run_task, timeout=2.0)
    assert chunks and chunks[-1].exit_code == 130
    assert reg.get_run("live1")["status"] == "cancelled"
