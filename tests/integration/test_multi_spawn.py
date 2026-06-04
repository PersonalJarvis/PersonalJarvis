"""Integration-Test: MultiSpawnTool (CL-10, Phase 5).

Nutzt FakeHarness statt echter openclaw/codex-CLI. HarnessManager wird
mit injizierten Fakes bestückt, damit Tests deterministisch und offline
laufen.
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.protocols import ExecutionContext, Tool
from jarvis.harness.manager import HarnessManager
from jarvis.plugins.tool.multi_spawn import MultiSpawnTool
from tests.fixtures.harness.fake_harness import FakeHarness


def _make_manager_with_fake(bus: EventBus, harness_name: str, fake: FakeHarness) -> HarnessManager:
    """Baut einen HarnessManager, der `harness_name` immer als Fresh-Copy
    der gegebenen FakeHarness instanziiert — sodass `dispatch` N-fach
    dieselbe Fake-Klasse abruft ohne Konflikt."""
    mgr = HarnessManager(bus=bus)
    mgr._loaded = True
    mgr._classes[harness_name] = type(fake)
    mgr._instances[harness_name] = fake
    return mgr


@pytest.fixture
def ctx():
    return ExecutionContext(
        trace_id=uuid4(),
        user_utterance="testing multi-spawn",
        config={},
        memory_read=None,
    )


@pytest.mark.asyncio
async def test_three_parallel_openclaw_calls(ctx):
    """3 parallele openclaw-Calls → aggregated output hat 3 Sections."""
    bus = EventBus()
    fake = FakeHarness(scripted_output="branch-A done\n")
    mgr = _make_manager_with_fake(bus, "openclaw", fake)
    tool = MultiSpawnTool(bus=bus, manager=mgr, max_output_chars=8000)

    result = await tool.execute(
        {
            "harness": "openclaw",
            "prompts": [
                "schreib die Tests für modul X",
                "schreib die Impl für modul X",
                "schreib die Docstrings für modul X",
            ],
            "aggregation": "merge",
        },
        ctx,
    )

    assert result.success is True
    combined = result.output["combined"]
    assert "Section 1/3:" in combined
    assert "Section 2/3:" in combined
    assert "Section 3/3:" in combined
    assert result.output["sections_total"] == 3
    assert result.output["sections_truncated"] == 0


@pytest.mark.asyncio
async def test_aggregation_merge_joins_all_outputs(ctx):
    """Merge-Mode konkateniert alle Section-Outputs mit '---'-Separator."""
    bus = EventBus()
    fake = FakeHarness(scripted_output="shared-body")
    mgr = _make_manager_with_fake(bus, "openclaw", fake)
    tool = MultiSpawnTool(bus=bus, manager=mgr)

    result = await tool.execute(
        {
            "harness": "openclaw",
            "prompts": ["p1", "p2"],
            "aggregation": "merge",
        },
        ctx,
    )

    assert result.success is True
    combined = result.output["combined"]
    assert combined.count("---") == 2
    assert "shared-body" in combined
    assert combined.startswith("---\nSection 1/2:")


@pytest.mark.asyncio
async def test_aggregation_first_success_returns_first_ok(ctx):
    """first_success-Mode returnt nur die gewinnende Section."""
    bus = EventBus()
    fake = FakeHarness(scripted_output="winner-output")
    mgr = _make_manager_with_fake(bus, "openclaw", fake)
    tool = MultiSpawnTool(bus=bus, manager=mgr)

    result = await tool.execute(
        {
            "harness": "openclaw",
            "prompts": ["p1", "p2", "p3"],
            "aggregation": "first_success",
        },
        ctx,
    )

    assert result.success is True
    combined = result.output["combined"]
    assert "winner-output" in combined
    assert result.output["winning_index"] is not None
    assert result.output["sections_total"] == 3
    assert result.output["sections_truncated"] == 2


@pytest.mark.asyncio
async def test_output_cap_truncates_large_outputs(ctx):
    """3 Sections × 5000 chars → aggregated ≤ 8000 chars + truncation marker."""
    bus = EventBus()
    big_body = "x" * 5000
    fake = FakeHarness(scripted_output=big_body)
    mgr = _make_manager_with_fake(bus, "openclaw", fake)
    tool = MultiSpawnTool(bus=bus, manager=mgr, max_output_chars=8000)

    result = await tool.execute(
        {
            "harness": "openclaw",
            "prompts": ["p1", "p2", "p3"],
            "aggregation": "merge",
        },
        ctx,
    )

    combined = result.output["combined"]
    assert len(combined) <= 8000
    assert "sections truncated" in combined
    assert result.output["sections_truncated"] >= 1


@pytest.mark.asyncio
async def test_tool_contract_compliance(ctx):
    """MultiSpawnTool entspricht strukturell dem Tool-Protocol."""
    tool = MultiSpawnTool(manager=HarnessManager())

    assert isinstance(tool, Tool)
    assert tool.name == "multi_spawn"
    assert tool.risk_tier == "monitor"
    assert isinstance(tool.description, str) and tool.description
    assert isinstance(tool.schema, dict)
    assert tool.schema["required"] == ["harness", "prompts"]
    assert tool.schema["properties"]["prompts"]["minItems"] == 2
    assert tool.schema["properties"]["prompts"]["maxItems"] == 5


@pytest.mark.asyncio
async def test_missing_harness_fails(ctx):
    tool = MultiSpawnTool(manager=HarnessManager())
    result = await tool.execute(
        {"harness": "", "prompts": ["a", "b"]},
        ctx,
    )
    assert result.success is False
    assert "harness" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_too_few_prompts_fails(ctx):
    tool = MultiSpawnTool(manager=HarnessManager())
    result = await tool.execute(
        {"harness": "openclaw", "prompts": ["only-one"]},
        ctx,
    )
    assert result.success is False
    assert "prompt" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_merge_failure_propagates(ctx):
    """Wenn ein Section-Exit != 0, wird success=False gesetzt."""
    bus = EventBus()
    fake = FakeHarness(fail=True)
    mgr = _make_manager_with_fake(bus, "openclaw", fake)
    tool = MultiSpawnTool(bus=bus, manager=mgr)

    result = await tool.execute(
        {
            "harness": "openclaw",
            "prompts": ["p1", "p2"],
            "aggregation": "merge",
        },
        ctx,
    )
    assert result.success is False
    assert result.output["sections_total"] == 2
