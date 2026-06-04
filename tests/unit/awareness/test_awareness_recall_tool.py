"""Tests for ``jarvis.plugins.tool.awareness_recall.AwarenessRecallTool``.

Plan §7 (Phase A3): a read-only BM25 search across recent episodes,
called by the router brain when the user references earlier work. The
plan originally placed this in a sub-jarvis tier; Welle 4 removed that
tier, so the tool lives in ``ROUTER_TOOLS``.

Tests cover:
- Plan-mandated tool shape (name, risk_tier, schema, description hints).
- Hit / miss behaviour with seeded episodes.
- ``since_minutes`` filter correctness via a small clock-skew fixture.
- ``k`` cap honoured.
- Defensive path: ``recall_store=None`` yields ``success=False`` instead
  of throwing.
- Latency budget (loose).
- Routing-tier placement: in ``ROUTER_TOOLS``, not in any worker set.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest
import pytest_asyncio

from jarvis.memory import RecallStore
from jarvis.plugins.tool.awareness_recall import AwarenessRecallTool


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def store(tmp_path: Path):
    s = RecallStore(tmp_path / "awareness.db")
    await s.open()
    yield s
    await s.close()


async def _seed(store: RecallStore, *, summary: str, app: str, minutes_ago: int) -> int:
    """Insert one episode whose ``started_at_ns`` lies ``minutes_ago`` in the past."""
    now_ns = time.time_ns()
    delta_ns = minutes_ago * 60 * 1_000_000_000
    return await store.record_episode(
        started_at_ns=now_ns - delta_ns,
        ended_at_ns=now_ns - delta_ns + 1_000_000_000,
        trigger_kind="window_switch",
        summary=summary,
        frame_count=3,
        primary_app=app,
    )


# ---------------------------------------------------------------------------
# Plan-mandated tool shape
# ---------------------------------------------------------------------------

def test_name_is_awareness_recall() -> None:
    tool = AwarenessRecallTool(recall_store=None)
    assert tool.name == "awareness-recall"


def test_risk_tier_is_safe() -> None:
    tool = AwarenessRecallTool(recall_store=None)
    assert tool.risk_tier == "safe"


def test_schema_requires_query_only() -> None:
    tool = AwarenessRecallTool(recall_store=None)
    assert tool.schema["required"] == ["query"]
    assert "k" in tool.schema["properties"]
    assert "since_minutes" in tool.schema["properties"]


def test_schema_clamps_documented() -> None:
    """k and since_minutes must declare their bounds."""
    tool = AwarenessRecallTool(recall_store=None)
    props = tool.schema["properties"]
    assert props["k"]["minimum"] == 1
    assert props["k"]["maximum"] == 10
    assert props["since_minutes"]["minimum"] == 1
    assert props["since_minutes"]["maximum"] == 10080  # 7 days


def test_description_mentions_user_phrasing() -> None:
    """The description must hint at the user phrases the brain should map to it."""
    tool = AwarenessRecallTool(recall_store=None)
    descr = tool.description.lower()
    assert "vorhin" in descr or "earlier" in descr


# ---------------------------------------------------------------------------
# Defensive path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_recall_store_none_returns_unavailable_without_throwing() -> None:
    tool = AwarenessRecallTool(recall_store=None)
    result = await tool.execute({"query": "pipeline"}, ctx=None)
    assert result.success is False
    assert result.error is not None
    assert "unavailable" in result.error.lower()


# ---------------------------------------------------------------------------
# Hit / miss
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_episodes_returns_friendly_empty(store: RecallStore) -> None:
    tool = AwarenessRecallTool(recall_store=store)
    result = await tool.execute({"query": "pipeline"}, ctx=None)
    assert result.success is True
    assert "no episodes" in result.output.lower()


@pytest.mark.asyncio
async def test_match_returns_markdown_with_app_name(store: RecallStore) -> None:
    await _seed(store, summary="pipeline refactor on event bus", app="code.exe", minutes_ago=5)
    tool = AwarenessRecallTool(recall_store=store)
    result = await tool.execute({"query": "pipeline"}, ctx=None)
    assert result.success is True
    assert "code.exe" in result.output
    assert "pipeline" in result.output.lower()
    assert result.output.lstrip().startswith("Found")


@pytest.mark.asyncio
async def test_match_includes_local_hhmm(store: RecallStore) -> None:
    await _seed(store, summary="working on awareness recall", app="code.exe", minutes_ago=1)
    tool = AwarenessRecallTool(recall_store=store)
    result = await tool.execute({"query": "awareness"}, ctx=None)
    assert result.success is True
    # Look for HH:MM-shaped substring in the output.
    import re
    assert re.search(r"\b\d{2}:\d{2}\b", result.output) is not None


# ---------------------------------------------------------------------------
# since_minutes filter
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_since_minutes_drops_old_matches(store: RecallStore) -> None:
    await _seed(store, summary="pipeline ancient", app="code.exe", minutes_ago=180)
    await _seed(store, summary="pipeline recent", app="code.exe", minutes_ago=10)
    tool = AwarenessRecallTool(recall_store=store)
    result = await tool.execute(
        {"query": "pipeline", "since_minutes": 60},
        ctx=None,
    )
    assert result.success is True
    assert "pipeline recent" in result.output
    assert "pipeline ancient" not in result.output


@pytest.mark.asyncio
async def test_default_since_minutes_is_24h(store: RecallStore) -> None:
    """An episode 6h old must be visible without explicit since_minutes."""
    await _seed(store, summary="pipeline somewhere mid-day", app="code.exe", minutes_ago=360)
    tool = AwarenessRecallTool(recall_store=store)
    result = await tool.execute({"query": "pipeline"}, ctx=None)
    assert result.success is True
    assert "pipeline somewhere mid-day" in result.output


# ---------------------------------------------------------------------------
# k cap
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_k_cap_truncates_results(store: RecallStore) -> None:
    for i in range(5):
        await _seed(store, summary=f"pipeline iteration {i}", app="code.exe", minutes_ago=i + 1)
    tool = AwarenessRecallTool(recall_store=store)
    result = await tool.execute({"query": "pipeline", "k": 2}, ctx=None)
    # The output has one header line plus one line per snippet.
    bullet_lines = [ln for ln in result.output.splitlines() if ln.startswith("- ")]
    assert len(bullet_lines) == 2


@pytest.mark.asyncio
async def test_k_above_max_is_clamped(store: RecallStore) -> None:
    for i in range(12):
        await _seed(store, summary=f"pipeline iter {i}", app="code.exe", minutes_ago=i + 1)
    tool = AwarenessRecallTool(recall_store=store)
    result = await tool.execute({"query": "pipeline", "k": 9999}, ctx=None)
    bullet_lines = [ln for ln in result.output.splitlines() if ln.startswith("- ")]
    assert len(bullet_lines) <= 10


# ---------------------------------------------------------------------------
# Latency (loose budget, CI-tolerant)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_latency_under_loose_budget(store: RecallStore) -> None:
    """100 episodes, search p95 well under 300ms even with disk + asyncio overhead."""
    for i in range(100):
        await _seed(
            store,
            summary=f"pipeline iteration {i} working on something",
            app="code.exe",
            minutes_ago=(i % 1000) + 1,
        )
    tool = AwarenessRecallTool(recall_store=store)
    durations_ms: list[float] = []
    for _ in range(20):
        t0 = time.perf_counter()
        await tool.execute({"query": "pipeline"}, ctx=None)
        durations_ms.append((time.perf_counter() - t0) * 1000)
    durations_ms.sort()
    p95 = durations_ms[int(len(durations_ms) * 0.95)]
    assert p95 < 300.0, f"p95 latency {p95:.2f}ms exceeds 300ms budget"


# ---------------------------------------------------------------------------
# Routing-tier placement
# ---------------------------------------------------------------------------

def test_in_router_tools() -> None:
    from jarvis.brain import factory
    assert "awareness-recall" in factory.ROUTER_TOOLS


def test_not_in_legacy_sub_tools() -> None:
    """Welle 4 removed the sub tier; the legacy frozenset must not exist or
    must not contain awareness-recall if it lingers as a compat shim."""
    from jarvis.brain import factory
    sub_tools = getattr(factory, "SUB_TOOLS", frozenset())
    assert "awareness-recall" not in sub_tools
