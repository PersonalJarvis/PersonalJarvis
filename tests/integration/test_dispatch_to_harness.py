"""Integration-Test: dispatch_to_harness-Tool + FakeHarness."""
from __future__ import annotations

from uuid import uuid4

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.protocols import ExecutionContext
from jarvis.harness.manager import HarnessManager
from jarvis.plugins.tool.dispatch_to_harness import DispatchToHarnessTool
from tests.fixtures.harness.fake_harness import FakeHarness


def _make_manager_with_fakes(bus: EventBus, fakes: dict) -> HarnessManager:
    mgr = HarnessManager(bus=bus)
    mgr._loaded = True
    for name, inst in fakes.items():
        mgr._classes[name] = type(inst)
        mgr._instances[name] = inst
    return mgr


@pytest.fixture
def ctx():
    return ExecutionContext(
        trace_id=uuid4(),
        user_utterance="testing",
        config={},
        memory_read=None,
    )


@pytest.mark.asyncio
async def test_single_harness_success(ctx):
    bus = EventBus()
    mgr = _make_manager_with_fakes(bus, {
        "openclaw": FakeHarness(scripted_output="Build läuft durch."),
    })
    tool = DispatchToHarnessTool(bus=bus, manager=mgr, max_output_chars=4000)

    result = await tool.execute(
        {"harness": "openclaw", "prompt": "Prüfe den Build."},
        ctx,
    )
    assert result.success is True
    assert result.output["harness"] == "openclaw"
    assert "Build läuft durch." in result.output["stdout"]


@pytest.mark.asyncio
async def test_harness_failure_returns_error(ctx):
    bus = EventBus()
    mgr = _make_manager_with_fakes(bus, {
        "codex": FakeHarness(fail=True),
    })
    tool = DispatchToHarnessTool(bus=bus, manager=mgr)

    result = await tool.execute({"harness": "codex", "prompt": "x"}, ctx)
    assert result.success is False
    assert "exit" in (result.error or "")


@pytest.mark.asyncio
async def test_parallel_harnesses_aggregate(ctx):
    bus = EventBus()
    mgr = _make_manager_with_fakes(bus, {
        "openclaw": FakeHarness(scripted_output="claude-out"),
        "codex": FakeHarness(scripted_output="codex-out"),
    })
    tool = DispatchToHarnessTool(bus=bus, manager=mgr)

    result = await tool.execute(
        {
            "harness": "openclaw",  # ignored wenn parallel_harnesses gesetzt
            "prompt": "same task",
            "parallel_harnesses": ["openclaw", "codex"],
        },
        ctx,
    )
    assert result.success is True
    combined = result.output["combined"]
    assert "openclaw" in combined
    assert "codex" in combined
    assert "claude-out" in combined
    assert "codex-out" in combined


@pytest.mark.asyncio
async def test_missing_prompt_fails(ctx):
    tool = DispatchToHarnessTool(bus=EventBus(), manager=HarnessManager())
    result = await tool.execute({"harness": "openclaw", "prompt": ""}, ctx)
    assert result.success is False


@pytest.mark.asyncio
async def test_output_trim_for_large_stdout(ctx):
    bus = EventBus()
    long = "x" * 20_000
    mgr = _make_manager_with_fakes(bus, {
        "fake": FakeHarness(scripted_output=long),
    })
    tool = DispatchToHarnessTool(bus=bus, manager=mgr, max_output_chars=1000)
    result = await tool.execute({"harness": "fake", "prompt": "p"}, ctx)
    assert result.success is True
    assert len(result.output["stdout"]) < 2000
    assert "gekürzt" in result.output["stdout"]
