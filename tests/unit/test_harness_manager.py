"""Unit-Tests für HarnessManager mit FakeHarness."""
from __future__ import annotations

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.events import HarnessCompleted, HarnessDispatched, HarnessProgress
from jarvis.core.protocols import HarnessTask
from jarvis.harness.manager import HarnessManager
from tests.fixtures.harness.fake_harness import FakeHarness


def _patch_manager_with_fakes(mgr: HarnessManager, instances: dict) -> None:
    mgr._loaded = True
    for name, inst in instances.items():
        mgr._classes[name] = type(inst)
        mgr._instances[name] = inst


@pytest.mark.asyncio
async def test_dispatch_yields_progress_and_final():
    bus = EventBus()
    mgr = HarnessManager(bus=bus)
    fake = FakeHarness(scripted_output="hello world")
    _patch_manager_with_fakes(mgr, {"fake": fake})

    results = []
    async for r in mgr.dispatch("fake", HarnessTask(prompt="t")):
        results.append(r)

    assert len(results) >= 2  # chunks + final
    assert results[-1].is_final
    assert results[-1].exit_code == 0
    assert fake.invocations[0].prompt == "t"


@pytest.mark.asyncio
async def test_dispatch_publishes_events():
    bus = EventBus()
    mgr = HarnessManager(bus=bus)
    fake = FakeHarness(scripted_output="abc")
    _patch_manager_with_fakes(mgr, {"fake": fake})

    events: list = []
    bus.subscribe(HarnessDispatched, lambda e: events.append(("D", e)))
    bus.subscribe(HarnessProgress, lambda e: events.append(("P", e)))
    bus.subscribe(HarnessCompleted, lambda e: events.append(("C", e)))

    async for _ in mgr.dispatch("fake", HarnessTask(prompt="x")):
        pass

    kinds = [k for k, _ in events]
    assert kinds[0] == "D"
    assert "C" in kinds
    assert kinds.count("P") >= 1


@pytest.mark.asyncio
async def test_dispatch_parallel_merge():
    bus = EventBus()
    mgr = HarnessManager(bus=bus)
    _patch_manager_with_fakes(mgr, {
        "fake-a": FakeHarness(scripted_output="A-out"),
        "fake-b": FakeHarness(scripted_output="B-out"),
    })

    collected = []
    async for name, res in mgr.dispatch_parallel(
        ["fake-a", "fake-b"],
        HarnessTask(prompt="p"),
    ):
        collected.append((name, res))

    harnesses = {n for n, _ in collected}
    assert harnesses == {"fake-a", "fake-b"}
    finals = [r for _, r in collected if r.is_final]
    assert len(finals) == 2


@pytest.mark.asyncio
async def test_dispatch_unknown_harness_raises():
    mgr = HarnessManager()
    mgr._loaded = True
    with pytest.raises(KeyError):
        async for _ in mgr.dispatch("does-not-exist", HarnessTask(prompt="x")):
            pass


@pytest.mark.asyncio
async def test_fail_harness_still_yields_final():
    mgr = HarnessManager()
    fake = FakeHarness(fail=True)
    _patch_manager_with_fakes(mgr, {"broken": fake})

    results = []
    async for r in mgr.dispatch("broken", HarnessTask(prompt="x")):
        results.append(r)

    assert results[-1].is_final
    assert results[-1].exit_code == 1
