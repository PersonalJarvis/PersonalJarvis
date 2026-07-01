"""Unit tests for the awareness L2 persistence (phase A2 slice A, plan §6).

Covers schema.sql idempotency, FTS5 trigger sync, round-trip consistency, and
the ORDER/LIMIT behavior of `record_frame`, `record_episode`, `recent_episodes`
and `search_episodes`.

Plus: B2 atomic-buffer regression (Codex BLOCKER B2 fix, 2026-05-11).
Verifies end-to-end via the StoryTracker with a real ``RecallStore``,
that a frame pushed WHILE a flush is in progress lands in the NEXT
episode, not the current one, and is never lost.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest_asyncio

from jarvis.awareness.config import AwarenessConfig, AwarenessStoryConfig
from jarvis.awareness.manager import AwarenessManager
from jarvis.awareness.state import FrameSnapshot
from jarvis.awareness.story import StoryTracker
from jarvis.core.bus import EventBus
from jarvis.core.events import FrameUpdated, IdleEntered
from jarvis.memory import RecallStore


@pytest_asyncio.fixture
async def tmp_db_path(tmp_path: Path) -> Path:
    """Per-test isolated DB path — avoids cross-test bleed."""
    return tmp_path / "awareness.db"


@pytest_asyncio.fixture
async def recall_store(tmp_db_path: Path):
    """RecallStore async context manager as a fixture (open + close)."""
    store = RecallStore(tmp_db_path)
    await store.open()
    yield store
    await store.close()


async def test_schema_idempotent(tmp_db_path: Path) -> None:
    """Calling `open()` twice in a row must not crash — the IF NOT EXISTS guarantee.

    Simulates an app restart: the DB already exists with all tables, a
    repeated `executescript(schema.sql)` must not disturb the existing
    data and definitions.
    """
    store_a = RecallStore(tmp_db_path)
    await store_a.open()
    await store_a.record_episode(
        started_at_ns=1_000_000_000,
        ended_at_ns=2_000_000_000,
        trigger_kind="window_switch",
        summary="initial",
        frame_count=1,
        primary_app="vscode.exe",
    )
    await store_a.close()

    # Second open() — no exception, data is preserved.
    store_b = RecallStore(tmp_db_path)
    await store_b.open()
    rows = await store_b.recent_episodes(limit=10)
    assert len(rows) == 1
    assert rows[0]["summary"] == "initial"
    await store_b.close()


async def test_record_episode_returns_id(recall_store: RecallStore) -> None:
    """`record_episode` returns a positive integer rowid."""
    episode_id = await recall_store.record_episode(
        started_at_ns=1_000_000_000,
        ended_at_ns=1_500_000_000,
        trigger_kind="window_switch",
        summary="You are working on pipeline.py in VS Code.",
        frame_count=12,
        primary_app="Code.exe",
    )
    assert isinstance(episode_id, int)
    assert episode_id > 0


async def test_recent_episodes_descending(recall_store: RecallStore) -> None:
    """`recent_episodes(limit=2)` returns the 2 newest in DESC order."""
    base_ns = 1_000_000_000_000
    # Three episodes with ascending start time
    for i in range(3):
        await recall_store.record_episode(
            started_at_ns=base_ns + i * 1_000_000_000,
            ended_at_ns=base_ns + i * 1_000_000_000 + 500_000_000,
            trigger_kind="window_switch",
            summary=f"episode-{i}",
            frame_count=5,
            primary_app="Code.exe",
        )

    rows = await recall_store.recent_episodes(limit=2)
    assert len(rows) == 2
    # Newest first → index 2, then 1
    assert rows[0]["summary"] == "episode-2"
    assert rows[1]["summary"] == "episode-1"
    assert rows[0]["started_at_ns"] > rows[1]["started_at_ns"]


async def test_recent_episodes_since_ns_filter(recall_store: RecallStore) -> None:
    """`since_ns=mid` filters on a >= comparison → only 2 of 3 episodes."""
    base_ns = 1_000_000_000_000
    timestamps = [base_ns, base_ns + 1_000_000_000, base_ns + 2_000_000_000]
    for i, ts in enumerate(timestamps):
        await recall_store.record_episode(
            started_at_ns=ts,
            ended_at_ns=ts + 100_000_000,
            trigger_kind="timer",
            summary=f"ep-{i}",
            frame_count=1,
            primary_app="x.exe",
        )

    # since=middle timestamp → ep-1 and ep-2 (>= mid)
    rows = await recall_store.recent_episodes(limit=10, since_ns=timestamps[1])
    assert len(rows) == 2
    summaries = {r["summary"] for r in rows}
    assert summaries == {"ep-1", "ep-2"}


async def test_search_episodes_fts_match(recall_store: RecallStore) -> None:
    """FTS5 MATCH over the summary hits only the matching episode."""
    await recall_store.record_episode(
        started_at_ns=1_000_000_000,
        ended_at_ns=1_500_000_000,
        trigger_kind="window_switch",
        summary="Python coding session in VS Code with tests",
        frame_count=20,
        primary_app="Code.exe",
    )
    await recall_store.record_episode(
        started_at_ns=2_000_000_000,
        ended_at_ns=2_500_000_000,
        trigger_kind="idle_entered",
        summary="Cooking dinner break, browser on a recipe page",
        frame_count=8,
        primary_app="firefox.exe",
    )

    rows = await recall_store.search_episodes(query="Python", limit=10)
    assert len(rows) == 1
    assert "Python" in rows[0]["summary"]
    assert rows[0]["primary_app"] == "Code.exe"


async def test_record_frame_persists(recall_store: RecallStore) -> None:
    """`record_frame` writes the row and the data can be read back directly."""
    frame_id = await recall_store.record_frame(
        window_title="pipeline.py - Visual Studio Code",
        process_name="Code.exe",
        timestamp_ns=1_234_567_890_000,
        salience_score=75,
        metadata={"git_branch": "awareness/phase-a2", "lines": 250},
    )
    assert isinstance(frame_id, int)
    assert frame_id > 0

    conn = recall_store._require_conn()
    cur = await conn.execute(
        "SELECT * FROM awareness_frames WHERE id = ?",
        (frame_id,),
    )
    row = await cur.fetchone()
    await cur.close()
    assert row is not None
    assert row["window_title"] == "pipeline.py - Visual Studio Code"
    assert row["process_name"] == "Code.exe"
    assert row["timestamp_ns"] == 1_234_567_890_000
    assert row["salience_score"] == 75
    # metadata_json is JSON-encoded
    import json
    meta = json.loads(row["metadata_json"])
    assert meta == {"git_branch": "awareness/phase-a2", "lines": 250}


# ----------------------------------------------------------------------------
# B2 atomic-buffer regression (Codex BLOCKER, 2026-05-11)
# ----------------------------------------------------------------------------
#
# Before (bug): EpisodeBuilder accumulated frames in a shared
# list; with a concurrent flush + a new FrameUpdated, a frame could be lost
# between the snapshot and the buffer reset.
# After (fix): EpisodeBuilder.detach_frames/detach_events atomically swap the
# internal lists for fresh buckets; StoryTracker._extract_snapshot_locked
# uses these, then sets _builder=None. A concurrent on_frame_updated only
# gets the lock after the snapshot — and sees builder=None, which leads to
# a fresh builder for the next frame.
#
# Test setup: StoryTracker + a real RecallStore + a slow verdichter that
# blocks for 300ms. Frame 1 before the flush, frame 2 WHILE the verdichter
# sleeps. AC: episode 1 contains frame 1, frame 2 lives in the new builder;
# after stop(), episode 2 is persisted with frame 2. No frame disappears.


@dataclass
class _SlowB2Verdichter:
    """Verdichter fake for the B2 test — sleeps for ``sleep_s`` during
    ``call`` and returns a unique summary so the persisted episodes can be
    unambiguously matched in the test assertions.
    """
    sleep_s: float = 0.3
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def call(
        self,
        *,
        frames: list[dict[str, Any]],
        events: list[dict[str, Any]],
        primary_app: str,
    ) -> tuple[str, dict[str, Any]]:
        self.calls.append({
            "frames_n": len(frames),
            "primary_app": primary_app,
            "titles": [f.get("window_title") for f in frames],
        })
        await asyncio.sleep(self.sleep_s)
        summary = f"summary-{len(self.calls)}"
        return summary, {"tokens_in": 1, "tokens_out": 1, "duration_ms": 0}


def _make_frame_for_b2(
    *,
    title: str,
    process: str = "Code.exe",
    ts_ns: int | None = None,
) -> FrameSnapshot:
    """Helper — minimal FrameSnapshot for the B2 test."""
    return FrameSnapshot(
        timestamp_ns=ts_ns if ts_ns is not None else time.time_ns(),
        active_window_title=title,
        active_process_name=process,
        active_pid=1000,
        is_capture_allowed=True,
    )


async def test_frame_during_flush_lands_in_next_episode_buffer(
    tmp_db_path: Path,
) -> None:
    """B2 AC: a frame enqueued WHILE the flush is running lands in the
    NEXT episode buffer — not lost, not in the current snapshot.

    End-to-end with a real ``RecallStore``. Verifies the full
    persistence chain:
    1. Frame F1 (``frame-1.py``) opens the builder.
    2. ``IdleEntered`` triggers a flush; the verdichter sleeps 300ms.
    3. WHILE the verdichter sleeps, F2 (``frame-2.py``) is pushed.
    4. Expected:
       - The verdichter call sees ONLY F1 (1 frame).
       - The live builder at the end of the push contains F2.
       - After verdichter completion: episode 1 in the DB with summary ``summary-1``.
       - After ``stop()``: episode 2 in the DB with summary ``summary-2``.
       - No frame is in both episodes, none disappears.
    """
    store = RecallStore(tmp_db_path)
    await store.open()
    try:
        bus = EventBus()
        manager = AwarenessManager(AwarenessConfig.default())
        verdichter = _SlowB2Verdichter(sleep_s=0.3)
        tracker = StoryTracker(
            manager=manager,
            bus=bus,
            recall=store,
            verdichter=verdichter,    # type: ignore[arg-type]
            config=AwarenessStoryConfig(episode_min_duration_s=1),
        )
        await tracker.start()

        try:
            # F1 before the flush — opens the builder.
            f1 = _make_frame_for_b2(title="frame-1.py", ts_ns=time.time_ns())
            manager.state.current_frame = f1
            await tracker._on_frame_updated(FrameUpdated(
                window_title=f1.active_window_title,
                process_name=f1.active_process_name,
                pid=f1.active_pid, is_capture_allowed=True,
            ))
            await asyncio.sleep(1.1)    # cross min_duration

            # Idle triggers a flush in its own task — the verdichter sleeps 300ms.
            flush_task = asyncio.create_task(tracker._on_idle_entered(
                IdleEntered(idle_since_ns=time.time_ns()),
            ))
            # Wait until verdichter has actually entered its sleep.
            for _ in range(100):
                await asyncio.sleep(0)
                if verdichter.calls:
                    break
            assert verdichter.calls, (
                "Pre-condition: verdichter must be mid-flight"
            )

            # F2 WHILE the verdichter sleeps. B2 fix: must go into the NEW
            # builder, NOT into the already snapshotted buffer, and must
            # not be lost.
            f2 = _make_frame_for_b2(title="frame-2.py", ts_ns=time.time_ns())
            manager.state.current_frame = f2
            await tracker._on_frame_updated(FrameUpdated(
                window_title=f2.active_window_title,
                process_name=f2.active_process_name,
                pid=f2.active_pid, is_capture_allowed=True,
            ))

            # The verdichter saw only F1.
            assert verdichter.calls[0]["frames_n"] == 1
            assert verdichter.calls[0]["titles"] == ["frame-1.py"]

            # F2 lives in the new builder.
            assert tracker._builder is not None
            live_titles = [
                f.active_window_title for f in tracker._builder.frames
            ]
            assert "frame-2.py" in live_titles, (
                f"B2-Regression: frame-2.py disappeared. Live builder "
                f"has {live_titles!r}"
            )

            # Wait out the verdichter sleep, episode 1 is persisted.
            await asyncio.wait_for(flush_task, timeout=2.0)
        finally:
            # Force-flush the remaining builder via the stop() trigger.
            await tracker.stop()

        # Two episodes persisted: episode 1 (idle, F1), episode 2 (stop, F2).
        rows = await store.recent_episodes(limit=10)
        assert len(rows) == 2, (
            f"B2 AC: expected 2 persisted episodes, found "
            f"{len(rows)} — did frame F2 get lost?"
        )
        # recent_episodes is DESC by started_at_ns → [F2 episode, F1 episode]
        assert rows[0]["summary"] == "summary-2"
        assert rows[0]["trigger_kind"] == "stop"
        assert rows[0]["frame_count"] == 1
        assert rows[1]["summary"] == "summary-1"
        assert rows[1]["trigger_kind"] == "idle_entered"
        assert rows[1]["frame_count"] == 1
    finally:
        await store.close()


async def test_record_episode_round_trip(recall_store: RecallStore) -> None:
    """All 8 fields of `record_episode` land 1:1 in `recent_episodes`."""
    payload = {
        "started_at_ns": 5_000_000_000,
        "ended_at_ns": 5_900_000_000,
        "trigger_kind": "brain_turn",
        "summary": "Round-trip test with all fields.",
        "frame_count": 17,
        "primary_app": "wezterm-gui.exe",
        "tokens_in": 642,
        "tokens_out": 158,
    }
    await recall_store.record_episode(**payload)

    rows = await recall_store.recent_episodes(limit=1)
    assert len(rows) == 1
    row = rows[0]
    for key, expected in payload.items():
        assert row[key] == expected, f"field {key} not preserved: {row[key]!r} != {expected!r}"
