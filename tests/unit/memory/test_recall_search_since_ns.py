"""Tests for the ``since_ns`` time filter on ``RecallStore.search_episodes``.

A3 (L3 Session Search) needs a time-bounded BM25 search so the
``awareness-recall`` tool can answer "what was I doing in the last hour?"
without leaking week-old episodes into the result set. The filter is
additive: ``since_ns=None`` preserves the original two-argument behaviour
verbatim.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from jarvis.memory import RecallStore


@pytest_asyncio.fixture
async def store(tmp_path: Path):
    s = RecallStore(tmp_path / "awareness.db")
    await s.open()
    yield s
    await s.close()


async def _seed_three_episodes(store: RecallStore) -> tuple[int, int, int]:
    """Insert three episodes with different ``started_at_ns`` and matching summaries.

    Returns the three rowids in insertion order (old, mid, recent).
    """
    old_id = await store.record_episode(
        started_at_ns=1_000_000_000_000,
        ended_at_ns=1_000_500_000_000,
        trigger_kind="window_switch",
        summary="working on pipeline old refactor",
        frame_count=3,
        primary_app="code.exe",
    )
    mid_id = await store.record_episode(
        started_at_ns=2_000_000_000_000,
        ended_at_ns=2_000_500_000_000,
        trigger_kind="window_switch",
        summary="pipeline error stuck on event bus",
        frame_count=5,
        primary_app="code.exe",
    )
    recent_id = await store.record_episode(
        started_at_ns=3_000_000_000_000,
        ended_at_ns=3_000_500_000_000,
        trigger_kind="idle_entered",
        summary="pipeline finally green tests passing",
        frame_count=2,
        primary_app="code.exe",
    )
    return old_id, mid_id, recent_id


# --- Backward-compat: since_ns=None must behave like the original signature.

@pytest.mark.asyncio
async def test_since_ns_none_returns_all_matches(store: RecallStore) -> None:
    await _seed_three_episodes(store)
    rows = await store.search_episodes(query="pipeline", limit=10, since_ns=None)
    assert len(rows) == 3


@pytest.mark.asyncio
async def test_signature_without_since_ns_still_works(store: RecallStore) -> None:
    """Callers that pre-date A3 must keep working unchanged."""
    await _seed_three_episodes(store)
    rows = await store.search_episodes(query="pipeline", limit=10)
    assert len(rows) == 3


# --- Time filter behaviour.

@pytest.mark.asyncio
async def test_since_ns_drops_older_episodes(store: RecallStore) -> None:
    _, mid_id, recent_id = await _seed_three_episodes(store)
    rows = await store.search_episodes(
        query="pipeline",
        limit=10,
        since_ns=2_000_000_000_000,
    )
    ids = {r["id"] for r in rows}
    assert ids == {mid_id, recent_id}


@pytest.mark.asyncio
async def test_since_ns_is_inclusive(store: RecallStore) -> None:
    """An episode whose started_at_ns equals since_ns must still be returned."""
    old_id, _, _ = await _seed_three_episodes(store)
    rows = await store.search_episodes(
        query="pipeline",
        limit=10,
        since_ns=1_000_000_000_000,
    )
    assert old_id in {r["id"] for r in rows}


@pytest.mark.asyncio
async def test_since_ns_filters_combined_with_query(store: RecallStore) -> None:
    """The filter must AND with MATCH — no time-only matches leak in."""
    await _seed_three_episodes(store)
    # Add an episode in the recent window but with a non-matching summary.
    await store.record_episode(
        started_at_ns=3_500_000_000_000,
        ended_at_ns=3_500_500_000_000,
        trigger_kind="window_switch",
        summary="reading documentation about caching",
        frame_count=1,
        primary_app="msedge.exe",
    )
    rows = await store.search_episodes(
        query="pipeline",
        limit=10,
        since_ns=2_000_000_000_000,
    )
    summaries = {r["summary"] for r in rows}
    assert all("pipeline" in s for s in summaries)
    assert "reading documentation about caching" not in summaries


@pytest.mark.asyncio
async def test_since_ns_future_returns_empty(store: RecallStore) -> None:
    await _seed_three_episodes(store)
    rows = await store.search_episodes(
        query="pipeline",
        limit=10,
        since_ns=9_999_999_999_999,
    )
    assert rows == []


@pytest.mark.asyncio
async def test_empty_query_returns_empty_regardless_of_since(store: RecallStore) -> None:
    """An empty query short-circuits before the time filter is even applied."""
    await _seed_three_episodes(store)
    rows = await store.search_episodes(
        query="   ",
        limit=10,
        since_ns=0,
    )
    assert rows == []
