"""Tests fuer jarvis.plugins.tool.awareness_snapshot.AwarenessSnapshotTool.

Verbindlich aus Plan §5: Tool ist SYNCHRON, KEIN Brain-Call, KEIN IO.
Plus zwei Hard-Negatives:
  1. Tool MUSS in ROUTER_TOOLS sein.
  2. Tool DARF NICHT in SUB_TOOLS sein (Sub-Jarvis ist stateless).
"""
from __future__ import annotations

import time

import pytest

from jarvis.awareness.config import AwarenessConfig
from jarvis.awareness.manager import AwarenessManager
from jarvis.awareness.state import FrameSnapshot
from jarvis.plugins.tool.awareness_snapshot import AwarenessSnapshotTool


def _make_manager_with_frame() -> AwarenessManager:
    manager = AwarenessManager(AwarenessConfig.default())
    manager.state.current_frame = FrameSnapshot(
        timestamp_ns=time.time_ns(),
        active_window_title="pipeline.py - Visual Studio Code",
        active_process_name="code.exe",
        active_pid=1234,
        is_capture_allowed=True,
    )
    return manager


# --- Plan-§5 verbindliche Properties ----------------------------------------

def test_name_is_awareness_snapshot() -> None:
    tool = AwarenessSnapshotTool(manager=AwarenessManager(AwarenessConfig.default()))
    assert tool.name == "awareness-snapshot"


def test_risk_tier_is_safe() -> None:
    tool = AwarenessSnapshotTool(manager=AwarenessManager(AwarenessConfig.default()))
    assert tool.risk_tier == "safe"


def test_schema_is_empty() -> None:
    """Plan §5: Tool nimmt keine Args."""
    tool = AwarenessSnapshotTool(manager=AwarenessManager(AwarenessConfig.default()))
    assert tool.schema == {"type": "object", "properties": {}, "required": []}


def test_description_mentions_state_first_use() -> None:
    """Plan §5 description: 'NUTZE das BEVOR du den User nach Kontext fragst'."""
    tool = AwarenessSnapshotTool(manager=AwarenessManager(AwarenessConfig.default()))
    assert "BEVOR" in tool.description or "vor" in tool.description.lower()


# --- Verhalten --------------------------------------------------------------

@pytest.mark.asyncio
async def test_returns_synchronously_without_brain_call() -> None:
    """Tool gibt sofort zurueck — Output enthaelt window_title."""
    tool = AwarenessSnapshotTool(manager=_make_manager_with_frame())
    result = await tool.execute({}, ctx=None)
    assert result.success is True
    assert "code.exe" in result.output or "Visual Studio Code" in result.output


@pytest.mark.asyncio
async def test_returns_empty_string_when_no_frame() -> None:
    """Ohne current_frame: snapshot_for_prompt liefert '' — Tool meldet success=True."""
    tool = AwarenessSnapshotTool(manager=AwarenessManager(AwarenessConfig.default()))
    result = await tool.execute({}, ctx=None)
    assert result.success is True
    assert result.output == ""


@pytest.mark.asyncio
async def test_1000_calls_under_50ms_p95() -> None:
    """Plan §5 AC: 1000 Calls in <50ms-p95."""
    tool = AwarenessSnapshotTool(manager=_make_manager_with_frame())
    durations_ms: list[float] = []
    for _ in range(1000):
        t0 = time.perf_counter()
        await tool.execute({}, ctx=None)
        durations_ms.append((time.perf_counter() - t0) * 1000)

    durations_ms.sort()
    p95 = durations_ms[int(len(durations_ms) * 0.95)]
    assert p95 < 50.0, f"p95 latency {p95:.2f}ms exceeds 50ms budget"


# --- Hard-Negatives (Plan §5 verbindlich) -----------------------------------

def test_NOT_in_SUB_TOOLS() -> None:
    """Plan §5 Hard-Negative: Sub-Jarvis ist stateless — kein awareness-snapshot."""
    from jarvis.brain import factory
    sub_tools = getattr(factory, "SUB_TOOLS", frozenset())
    assert "awareness-snapshot" not in sub_tools


def test_in_ROUTER_TOOLS() -> None:
    """Plan §5: awareness-snapshot ist Router-Tier-Only."""
    from jarvis.brain import factory
    router_tools = getattr(factory, "ROUTER_TOOLS", frozenset())
    assert "awareness-snapshot" in router_tools
