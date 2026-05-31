"""Unit-Tests fuer die Awareness-L2-Persistence (Phase A2 Slice A, Plan §6).

Deckt schema.sql-Idempotenz, FTS5-Trigger-Sync, Round-Trip-Konsistenz und
ORDER/LIMIT-Verhalten von `record_frame`, `record_episode`, `recent_episodes`
und `search_episodes` ab.

Plus: B2-Atomic-Buffer-Regression (Codex-BLOCKER B2-Fix, 2026-05-11).
Verifiziert end-to-end ueber den StoryTracker mit echter ``RecallStore``,
dass ein Frame der WAEHREND eines Flushs gepusht wird in der NAECHSTEN
Episode landet, nicht in der aktuellen und niemals verloren geht.
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
    """Pro-Test isolierter DB-Pfad — vermeidet Cross-Test-Bleed."""
    return tmp_path / "awareness.db"


@pytest_asyncio.fixture
async def recall_store(tmp_db_path: Path):
    """RecallStore async-Context-Manager als Fixture (open + close)."""
    store = RecallStore(tmp_db_path)
    await store.open()
    yield store
    await store.close()


async def test_schema_idempotent(tmp_db_path: Path) -> None:
    """`open()` zweimal nacheinander darf nicht crashen — IF NOT EXISTS-Garantie.

    Simuliert App-Restart: die DB existiert bereits mit allen Tabellen, ein
    erneutes `executescript(schema.sql)` darf die bestehenden Daten und
    Definitionen nicht stoeren.
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

    # Zweiter open() — keine Exception, Daten bleiben erhalten.
    store_b = RecallStore(tmp_db_path)
    await store_b.open()
    rows = await store_b.recent_episodes(limit=10)
    assert len(rows) == 1
    assert rows[0]["summary"] == "initial"
    await store_b.close()


async def test_record_episode_returns_id(recall_store: RecallStore) -> None:
    """`record_episode` returnt eine positive Integer-rowid."""
    episode_id = await recall_store.record_episode(
        started_at_ns=1_000_000_000,
        ended_at_ns=1_500_000_000,
        trigger_kind="window_switch",
        summary="Du arbeitest an pipeline.py in VS Code.",
        frame_count=12,
        primary_app="Code.exe",
    )
    assert isinstance(episode_id, int)
    assert episode_id > 0


async def test_recent_episodes_descending(recall_store: RecallStore) -> None:
    """`recent_episodes(limit=2)` liefert die 2 juengsten in DESC-Reihenfolge."""
    base_ns = 1_000_000_000_000
    # Drei Episodes mit aufsteigender Start-Zeit
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
    # Neueste zuerst → Index 2, dann 1
    assert rows[0]["summary"] == "episode-2"
    assert rows[1]["summary"] == "episode-1"
    assert rows[0]["started_at_ns"] > rows[1]["started_at_ns"]


async def test_recent_episodes_since_ns_filter(recall_store: RecallStore) -> None:
    """`since_ns=mid` filtert auf >=-Vergleich → nur 2 von 3 Episodes."""
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

    # since=mittlerer Timestamp → ep-1 und ep-2 (>= mid)
    rows = await recall_store.recent_episodes(limit=10, since_ns=timestamps[1])
    assert len(rows) == 2
    summaries = {r["summary"] for r in rows}
    assert summaries == {"ep-1", "ep-2"}


async def test_search_episodes_fts_match(recall_store: RecallStore) -> None:
    """FTS5-MATCH ueber Summary trifft nur die passende Episode."""
    await recall_store.record_episode(
        started_at_ns=1_000_000_000,
        ended_at_ns=1_500_000_000,
        trigger_kind="window_switch",
        summary="Python coding session in VS Code mit Tests",
        frame_count=20,
        primary_app="Code.exe",
    )
    await recall_store.record_episode(
        started_at_ns=2_000_000_000,
        ended_at_ns=2_500_000_000,
        trigger_kind="idle_entered",
        summary="Cooking dinner break, Browser auf Rezeptseite",
        frame_count=8,
        primary_app="firefox.exe",
    )

    rows = await recall_store.search_episodes(query="Python", limit=10)
    assert len(rows) == 1
    assert "Python" in rows[0]["summary"]
    assert rows[0]["primary_app"] == "Code.exe"


async def test_record_frame_persists(recall_store: RecallStore) -> None:
    """`record_frame` schreibt den Row und die Daten lassen sich direkt lesen."""
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
    # metadata_json ist JSON-encoded
    import json
    meta = json.loads(row["metadata_json"])
    assert meta == {"git_branch": "awareness/phase-a2", "lines": 250}


# ----------------------------------------------------------------------------
# B2-Atomic-Buffer-Regression (Codex-BLOCKER, 2026-05-11)
# ----------------------------------------------------------------------------
#
# Vorher (Bug): EpisodeBuilder akkumulierte Frames in einer gemeinsamen
# Liste; bei concurrent Flush + neuem FrameUpdated konnte ein Frame zwischen
# Snapshot und Buffer-Reset verloren gehen.
# Nachher (Fix): EpisodeBuilder.detach_frames/detach_events tauschen die
# internen Listen atomar mit fresh Buckets aus; StoryTracker._extract_snapshot_locked
# nutzt diese, dann setzt es _builder=None. Ein concurrent on_frame_updated
# bekommt den Lock erst nach dem Snapshot — und sieht builder=None, was zu
# einem frischen Builder fuer den naechsten Frame fuehrt.
#
# Test-Aufbau: StoryTracker + echte RecallStore + Slow-Verdichter, der
# 300ms blockt. Frame 1 vor Flush, Frame 2 WAEHREND des Verdichter-Sleeps.
# AC: Episode 1 enthaelt Frame 1, Frame 2 lebt in neuem Builder; nach
# stop() wird Episode 2 mit Frame 2 persistiert. Keine Frame verschwindet.


@dataclass
class _SlowB2Verdichter:
    """Verdichter-Fake fuer den B2-Test — schlaeft ``sleep_s`` waehrend
    ``call`` und gibt eine eindeutige Summary zurueck, damit die
    persistierten Episoden im Test-Assert eindeutig zuzuordnen sind.
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
    """Helper — minimaler FrameSnapshot fuer den B2-Test."""
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
    """B2-AC: Frame, der waehrend des Flushs eingereiht wird, landet im
    NAECHSTEN Episode-Buffer — nicht verloren, nicht im aktuellen Snapshot.

    End-to-End mit echter ``RecallStore``. Verifiziert die volle
    Persistence-Kette:
    1. Frame F1 (``frame-1.py``) oeffnet Builder.
    2. ``IdleEntered`` triggert Flush; Verdichter sleeps 300ms.
    3. WAEHREND der Verdichter sleeps wird F2 (``frame-2.py``) gepusht.
    4. Erwartet:
       - Verdichter-Call sieht NUR F1 (1 frame).
       - Live-Builder am Ende des Pushs enthaelt F2.
       - Nach Verdichter-Completion: Episode 1 in DB mit summary ``summary-1``.
       - Nach ``stop()``: Episode 2 in DB mit summary ``summary-2``.
       - Keine Frame in beiden Episoden, keine verschwunden.
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
            # F1 vor Flush — opens builder.
            f1 = _make_frame_for_b2(title="frame-1.py", ts_ns=time.time_ns())
            manager.state.current_frame = f1
            await tracker._on_frame_updated(FrameUpdated(
                window_title=f1.active_window_title,
                process_name=f1.active_process_name,
                pid=f1.active_pid, is_capture_allowed=True,
            ))
            await asyncio.sleep(1.1)    # cross min_duration

            # Idle triggert Flush in eigenem Task — Verdichter sleeps 300ms.
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

            # F2 WAEHREND Verdichter sleeps. B2-Fix: muss in NEUEN Builder
            # gehen, NICHT in den bereits gesnapshotteten Buffer und nicht
            # verloren.
            f2 = _make_frame_for_b2(title="frame-2.py", ts_ns=time.time_ns())
            manager.state.current_frame = f2
            await tracker._on_frame_updated(FrameUpdated(
                window_title=f2.active_window_title,
                process_name=f2.active_process_name,
                pid=f2.active_pid, is_capture_allowed=True,
            ))

            # Verdichter sah nur F1.
            assert verdichter.calls[0]["frames_n"] == 1
            assert verdichter.calls[0]["titles"] == ["frame-1.py"]

            # F2 lebt im neuen Builder.
            assert tracker._builder is not None
            live_titles = [
                f.active_window_title for f in tracker._builder.frames
            ]
            assert "frame-2.py" in live_titles, (
                f"B2-Regression: frame-2.py disappeared. Live builder "
                f"has {live_titles!r}"
            )

            # Verdichter-Sleep abwarten, Episode 1 persistiert.
            await asyncio.wait_for(flush_task, timeout=2.0)
        finally:
            # Force-flush des verbleibenden Builders via stop()-Trigger.
            await tracker.stop()

        # Zwei Episoden persistiert: Episode 1 (idle, F1), Episode 2 (stop, F2).
        rows = await store.recent_episodes(limit=10)
        assert len(rows) == 2, (
            f"B2-AC: erwartet 2 persistierte Episoden, gefunden "
            f"{len(rows)} — Frame F2 ging verloren?"
        )
        # recent_episodes ist DESC nach started_at_ns → [F2-Episode, F1-Episode]
        assert rows[0]["summary"] == "summary-2"
        assert rows[0]["trigger_kind"] == "stop"
        assert rows[0]["frame_count"] == 1
        assert rows[1]["summary"] == "summary-1"
        assert rows[1]["trigger_kind"] == "idle_entered"
        assert rows[1]["frame_count"] == 1
    finally:
        await store.close()


async def test_record_episode_round_trip(recall_store: RecallStore) -> None:
    """Alle 8 Felder von `record_episode` landen 1:1 in `recent_episodes`."""
    payload = {
        "started_at_ns": 5_000_000_000,
        "ended_at_ns": 5_900_000_000,
        "trigger_kind": "brain_turn",
        "summary": "Round-trip-Test mit allen Feldern.",
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
        assert row[key] == expected, f"Feld {key} nicht erhalten: {row[key]!r} != {expected!r}"
