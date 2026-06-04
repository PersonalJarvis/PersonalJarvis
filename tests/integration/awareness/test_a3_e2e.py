"""End-to-end test for Awareness Phase A3.

Goes through the full happy path without mocks: open a real SQLite-backed
``RecallStore``, persist three real episodes through ``record_episode``
(which fires the FTS5 trigger automatically), instantiate the real
``AwarenessRecallTool``, call ``execute`` with a real time-bounded query,
and assert on the rendered markdown output.

The point is to catch wiring drift — schema/trigger out of sync, sanitizer
swallowing valid queries, time arithmetic flipped sign, etc. — that unit
tests with mocked stores would miss.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest
import pytest_asyncio

from jarvis.memory import RecallStore
from jarvis.plugins.tool.awareness_recall import AwarenessRecallTool


@pytest_asyncio.fixture
async def store(tmp_path: Path):
    s = RecallStore(tmp_path / "awareness.db")
    await s.open()
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_end_to_end_search_finds_recent_writes_filtering_old_ones(
    store: RecallStore,
) -> None:
    """Three real episodes — one old, two recent — must filter and rank correctly."""
    now_ns = time.time_ns()
    minute_ns = 60 * 1_000_000_000

    # 3 hours ago: should be filtered out by since_minutes=60.
    await store.record_episode(
        started_at_ns=now_ns - 180 * minute_ns,
        ended_at_ns=now_ns - 179 * minute_ns,
        trigger_kind="window_switch",
        summary="pipeline refactor that should NOT appear in 60min window",
        frame_count=4,
        primary_app="code.exe",
    )
    # 20 minutes ago: must appear, lower BM25 rank than the very recent one.
    await store.record_episode(
        started_at_ns=now_ns - 20 * minute_ns,
        ended_at_ns=now_ns - 19 * minute_ns,
        trigger_kind="window_switch",
        summary="pipeline configure event bus subscribers",
        frame_count=6,
        primary_app="code.exe",
    )
    # 2 minutes ago: must appear.
    await store.record_episode(
        started_at_ns=now_ns - 2 * minute_ns,
        ended_at_ns=now_ns - 1 * minute_ns,
        trigger_kind="idle_entered",
        summary="pipeline tests finally green",
        frame_count=2,
        primary_app="code.exe",
    )

    tool = AwarenessRecallTool(recall_store=store)
    result = await tool.execute(
        {"query": "pipeline", "k": 5, "since_minutes": 60},
        ctx=None,
    )

    assert result.success is True
    out = result.output
    assert "pipeline configure event bus" in out
    assert "pipeline tests finally green" in out
    assert "should NOT appear" not in out
    # Markdown shape: header line then bullet snippets.
    bullets = [ln for ln in out.splitlines() if ln.startswith("- ")]
    assert len(bullets) == 2
    # Each bullet must carry the primary-app marker.
    assert all("code.exe" in line for line in bullets)


@pytest.mark.asyncio
async def test_end_to_end_no_match_yields_clear_message(store: RecallStore) -> None:
    now_ns = time.time_ns()
    minute_ns = 60 * 1_000_000_000
    await store.record_episode(
        started_at_ns=now_ns - 10 * minute_ns,
        ended_at_ns=now_ns - 9 * minute_ns,
        trigger_kind="window_switch",
        summary="reading documentation about caching strategies",
        frame_count=1,
        primary_app="msedge.exe",
    )
    tool = AwarenessRecallTool(recall_store=store)
    result = await tool.execute(
        {"query": "completely-unrelated-term", "since_minutes": 60},
        ctx=None,
    )
    assert result.success is True
    assert "no episodes" in result.output.lower()
