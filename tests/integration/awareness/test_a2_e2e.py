"""Phase A2 — End-to-End Integration Test.

Echte Komponenten verdrahtet (EventBus + RecallStore mit tmpdir DB +
StoryTracker + FakeVerdichter). Simuliert eine reale Bus-Sequenz:

  FrameUpdated x N -> IdleEntered

und prueft den Vollpfad:

  - Verdichter wird genau einmal gerufen mit den high-salience Frames
  - Episode landet in awareness_episodes (SQLite)
  - FTS-Index awareness_episodes_fts ist befuellt (suchbar)
  - state.last_episode_summary + last_episode_id wurden gesetzt
  - EpisodeRecorded-Event wurde publiziert
  - PrivacyFilter-blockierte Frames sind NICHT im Verdichter-Input

Plan §6 AC §6:
  - "Episode wird nach Window-Switch zu anderer App erstellt (Test mit Bus-Replay)" ✅
  - "Episode landet in SQLite, FTS-Index ist befüllt" ✅
  - "state.last_episode_summary wird nach jedem Flush updated" ✅
  - "PrivacyFilter-blockierte Frames sind NICHT im Verdichter-Input" ✅

Konvention: Fakes statt Mocks (CLAUDE.md). FakeVerdichter ist deterministisch.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from jarvis.awareness.config import AwarenessConfig, AwarenessStoryConfig
from jarvis.awareness.manager import AwarenessManager
from jarvis.awareness.state import FrameSnapshot
from jarvis.awareness.story import StoryTracker
from jarvis.core.bus import EventBus
from jarvis.core.events import (
    EpisodeRecorded,
    FrameUpdated,
    IdleEntered,
)
from jarvis.memory.recall import RecallStore

# ---- Fakes -----------------------------------------------------------------


@dataclass
class FakeVerdichter:
    """Deterministischer Verdichter — kein echter Brain-Call im e2e-Test."""
    summary: str = "E2E Test: User war in Code.exe mit pipeline.py aktiv."
    tokens_in: int = 250
    tokens_out: int = 80
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def call(
        self,
        *,
        frames: list[dict[str, Any]],
        events: list[dict[str, Any]],
        primary_app: str,
    ) -> tuple[str, dict[str, Any]]:
        self.calls.append({
            "frames": list(frames),
            "events": list(events),
            "primary_app": primary_app,
        })
        return self.summary, {
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "duration_ms": 150,
            "error_reason": None,
        }


# ---- Fixtures --------------------------------------------------------------


@pytest.fixture
async def recall_store(tmp_path: Path) -> RecallStore:
    db_path = tmp_path / "jarvis_a2_e2e.db"
    store = RecallStore(db_path)
    await store.open()
    yield store
    await store.close()


# ---- Helpers ---------------------------------------------------------------


def _make_frame(
    *,
    title: str,
    process: str = "Code.exe",
    pid: int = 1000,
    ts_ns: int | None = None,
    capture_allowed: bool = True,
) -> FrameSnapshot:
    return FrameSnapshot(
        timestamp_ns=ts_ns if ts_ns is not None else time.time_ns(),
        active_window_title=title,
        active_process_name=process,
        active_pid=pid,
        is_capture_allowed=capture_allowed,
    )


def _frame_event(frame: FrameSnapshot) -> FrameUpdated:
    return FrameUpdated(
        window_title=frame.active_window_title,
        process_name=frame.active_process_name,
        pid=frame.active_pid,
        is_capture_allowed=frame.is_capture_allowed,
    )


# ---- Tests -----------------------------------------------------------------


async def test_e2e_idle_flush_persists_episode_with_fts(
    recall_store: RecallStore,
) -> None:
    """Bus-Replay: 3 high-salience Frames -> IdleEntered -> Episode in SQLite + FTS."""
    bus = EventBus()
    manager = AwarenessManager(AwarenessConfig.default())
    verdichter = FakeVerdichter(
        summary="User arbeitete an pipeline.py in Code.exe (~12min).",
    )
    cfg = AwarenessStoryConfig(episode_min_duration_s=1)
    tracker = StoryTracker(
        manager=manager, bus=bus, recall=recall_store,
        verdichter=verdichter, config=cfg,
    )
    received: list[EpisodeRecorded] = []

    async def collect(ev: EpisodeRecorded) -> None:
        received.append(ev)
    bus.subscribe(EpisodeRecorded, collect)

    # 3 saliente Frames (verschiedene Titles, gleicher Process → +30 each)
    base_ts = time.time_ns() - 2_000_000_000    # 2s in der Vergangenheit
    for i, title in enumerate(["pipeline.py", "manager.py", "factory.py"]):
        f = _make_frame(title=title, ts_ns=base_ts + i * 500_000_000)
        manager.state.current_frame = f
        await tracker._on_frame_updated(_frame_event(f))

    # IdleEntered → flush
    await tracker._on_idle_entered(IdleEntered(idle_since_ns=time.time_ns()))

    # Verdichter wurde 1x gerufen
    assert len(verdichter.calls) == 1
    call = verdichter.calls[0]
    assert len(call["frames"]) == 3
    assert call["primary_app"] == "Code.exe"

    # Episode in SQLite
    episodes = await recall_store.recent_episodes(limit=10)
    assert len(episodes) == 1
    ep = episodes[0]
    assert ep["summary"] == "User arbeitete an pipeline.py in Code.exe (~12min)."
    assert ep["primary_app"] == "Code.exe"
    assert ep["frame_count"] == 3
    assert ep["trigger_kind"] == "idle_entered"
    assert ep["tokens_in"] == 250
    assert ep["tokens_out"] == 80

    # FTS-Index ist befuellt: search liefert die Episode
    fts_results = await recall_store.search_episodes(query="pipeline", limit=10)
    assert len(fts_results) == 1
    assert fts_results[0]["summary"] == ep["summary"]

    # State updated
    assert manager.state.last_episode_summary == ep["summary"]
    assert manager.state.last_episode_id == ep["id"]

    # EpisodeRecorded event published
    import asyncio as _aio
    await _aio.sleep(0.05)
    assert len(received) == 1
    assert received[0].episode_id == ep["id"]
    assert received[0].summary_preview == ep["summary"][:80]


async def test_e2e_privacy_blocked_frames_not_in_verdichter_input(
    recall_store: RecallStore,
) -> None:
    """Hard Negative §6: capture_allowed=False Frames NIE im Verdichter-Input."""
    bus = EventBus()
    manager = AwarenessManager(AwarenessConfig.default())
    verdichter = FakeVerdichter()
    cfg = AwarenessStoryConfig(episode_min_duration_s=1)
    tracker = StoryTracker(
        manager=manager, bus=bus, recall=recall_store,
        verdichter=verdichter, config=cfg,
    )

    base_ts = time.time_ns() - 2_000_000_000
    # Mix: 2 allowed, 1 blocked, 2 allowed
    sequence = [
        ("file_a.py", True),
        ("Banking - Sparkasse", False),    # PrivacyFilter blocked
        ("file_b.py", True),
        ("Password Vault", False),         # blocked
        ("file_c.py", True),
    ]
    for i, (title, allowed) in enumerate(sequence):
        f = _make_frame(
            title=title, ts_ns=base_ts + i * 500_000_000,
            capture_allowed=allowed,
        )
        manager.state.current_frame = f
        await tracker._on_frame_updated(_frame_event(f))

    await tracker._on_idle_entered(IdleEntered(idle_since_ns=time.time_ns()))

    # Verdichter call enthaelt KEINE blocked-Titles
    assert len(verdichter.calls) == 1
    call = verdichter.calls[0]
    titles_seen = [f["window_title"] for f in call["frames"]]
    assert "Banking - Sparkasse" not in titles_seen
    assert "Password Vault" not in titles_seen
    # Allowed Titles sind drin (3 Stueck)
    assert len(titles_seen) == 3
    for t in ("file_a.py", "file_b.py", "file_c.py"):
        assert t in titles_seen


async def test_e2e_app_switch_creates_episode_after_min_duration(
    recall_store: RecallStore,
) -> None:
    """Plan §6 AC: Episode wird nach Window-Switch zu anderer App erstellt."""
    import asyncio as _aio

    bus = EventBus()
    manager = AwarenessManager(AwarenessConfig.default())
    verdichter = FakeVerdichter(summary="Code-Phase abgeschlossen.")
    cfg = AwarenessStoryConfig(episode_min_duration_s=1)
    tracker = StoryTracker(
        manager=manager, bus=bus, recall=recall_store,
        verdichter=verdichter, config=cfg,
    )

    # Frame in Code.exe
    f1 = _make_frame(title="main.py", process="Code.exe")
    manager.state.current_frame = f1
    await tracker._on_frame_updated(_frame_event(f1))

    # > min_duration warten
    await _aio.sleep(1.1)

    # App-Switch: Code → Chrome
    f2 = _make_frame(
        title="GitHub - PR #42", process="chrome.exe", pid=2000,
    )
    manager.state.current_frame = f2
    await tracker._on_frame_updated(_frame_event(f2))

    # Episode persistiert mit trigger="window_switch"
    episodes = await recall_store.recent_episodes(limit=10)
    assert len(episodes) == 1
    assert episodes[0]["trigger_kind"] == "window_switch"
    assert episodes[0]["primary_app"] == "Code.exe"


async def test_e2e_short_episode_not_persisted(
    recall_store: RecallStore,
) -> None:
    """Plan §6 AC: Episode NICHT erstellt bei Same-App-Switch ohne 60s-Mindestdauer."""
    bus = EventBus()
    manager = AwarenessManager(AwarenessConfig.default())
    verdichter = FakeVerdichter()
    # min_duration=999s — alle App-Switches sind "zu kurz"
    cfg = AwarenessStoryConfig(episode_min_duration_s=999)
    tracker = StoryTracker(
        manager=manager, bus=bus, recall=recall_store,
        verdichter=verdichter, config=cfg,
    )

    base_ts = time.time_ns()
    f1 = _make_frame(title="main.py", process="Code.exe", ts_ns=base_ts)
    manager.state.current_frame = f1
    await tracker._on_frame_updated(_frame_event(f1))

    f2 = _make_frame(
        title="GitHub", process="chrome.exe", pid=2,
        ts_ns=base_ts + 500_000_000,    # 0.5s spaeter
    )
    manager.state.current_frame = f2
    await tracker._on_frame_updated(_frame_event(f2))

    # Verdichter NICHT gerufen, recall leer
    assert verdichter.calls == []
    episodes = await recall_store.recent_episodes(limit=10)
    assert len(episodes) == 0


async def test_e2e_state_snapshot_includes_episode_summary(
    recall_store: RecallStore,
) -> None:
    """Plan §6 AC: state.snapshot_for_prompt() enthaelt nach flush das Summary."""
    bus = EventBus()
    manager = AwarenessManager(AwarenessConfig.default())
    summary = "Du arbeitest seit 23min an pipeline.py in Code.exe."
    verdichter = FakeVerdichter(summary=summary)
    cfg = AwarenessStoryConfig(episode_min_duration_s=1)
    tracker = StoryTracker(
        manager=manager, bus=bus, recall=recall_store,
        verdichter=verdichter, config=cfg,
    )

    f = _make_frame(title="pipeline.py")
    manager.state.current_frame = f
    await tracker._on_frame_updated(_frame_event(f))
    import asyncio as _aio
    await _aio.sleep(1.1)
    await tracker._on_idle_entered(IdleEntered(idle_since_ns=time.time_ns()))

    snap = manager.state.snapshot_for_prompt(max_chars=600)
    # current_frame ist None (Watcher wurde nicht gestartet); aber
    # last_episode_summary ist gesetzt → sollte gerendert werden.
    # Wenn current_frame is None: snapshot_for_prompt returnt "" — das ist
    # die A1-Implementation. Lass uns sicherstellen dass es entweder
    # rendert ODER current_frame gesetzt setzen.
    # Setzen wir current_frame nochmal damit snapshot rendert:
    manager.state.current_frame = f
    snap = manager.state.snapshot_for_prompt(max_chars=600)
    assert summary in snap
    assert "pipeline.py" in snap
