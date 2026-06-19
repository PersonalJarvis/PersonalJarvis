"""Phase A2 — StoryTracker (jarvis/awareness/story.py).

Tests die Trigger-Logik (Salience-Filter, App-Switch, Idle, Hard-Timer,
Buffer-Overflow), den End-to-End-Pfad (Verdichter -> Recall -> State-Update
-> Bus-Event) sowie alle §6 Hard-Negatives (PrivacyFilter-Block,
Min-Duration-Skip, Verdichter-Exception-Handling).

Konvention: Fakes statt Mocks (CLAUDE.md). FakeRecall + FakeVerdichter
implementieren genau die Methoden die der Tracker aufruft.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from jarvis.awareness.config import AwarenessConfig, AwarenessStoryConfig
from jarvis.awareness.manager import AwarenessManager
from jarvis.awareness.state import FrameSnapshot
from jarvis.awareness.story import StoryTracker
from jarvis.core.bus import EventBus
from jarvis.core.events import (
    EpisodeRecorded,
    FrameUpdated,
    IdleEntered,
    ResponseGenerated,
)

# ---- Fakes -----------------------------------------------------------------


@dataclass
class FakeRecall:
    """Fake fuer RecallStore — sammelt record_episode-Calls."""
    episodes: list[dict[str, Any]] = field(default_factory=list)
    next_id: int = 1
    raise_on_record: bool = False

    async def record_episode(self, **kwargs: Any) -> int:
        if self.raise_on_record:
            raise RuntimeError("simulated recall failure")
        ep_id = self.next_id
        self.next_id += 1
        self.episodes.append({"id": ep_id, **kwargs})
        return ep_id


@dataclass
class FakeVerdichter:
    """Fake fuer Verdichter — gibt deterministische Summary + Usage zurueck."""
    summary: str = "Fake summary"
    tokens_in: int = 100
    tokens_out: int = 50
    duration_ms: int = 200
    raise_exc: Exception | None = None
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
            "events_n": len(events),
            "primary_app": primary_app,
        })
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.summary, {
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "duration_ms": self.duration_ms,
            "error_reason": None,
        }


# ---- Helpers ---------------------------------------------------------------


def _make_frame(
    *,
    title: str = "main.py - Visual Studio Code",
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


def _make_tracker(
    *,
    story_cfg: AwarenessStoryConfig | None = None,
    fake_recall: FakeRecall | None = None,
    fake_verdichter: FakeVerdichter | None = None,
) -> tuple[StoryTracker, AwarenessManager, EventBus, FakeRecall, FakeVerdichter]:
    bus = EventBus()
    manager = AwarenessManager(AwarenessConfig.default())
    recall = fake_recall or FakeRecall()
    verdichter = fake_verdichter or FakeVerdichter()
    cfg = story_cfg or AwarenessStoryConfig()
    tracker = StoryTracker(
        manager=manager,
        bus=bus,
        recall=recall,
        verdichter=verdichter,
        config=cfg,
    )
    return tracker, manager, bus, recall, verdichter


# ---- Tests: Lifecycle ------------------------------------------------------


async def test_init_does_not_crash() -> None:
    tracker, *_ = _make_tracker()
    assert tracker._started is False


async def test_start_subscribes_to_bus() -> None:
    tracker, _, bus, _, _ = _make_tracker()
    await tracker.start()
    try:
        # Bus sollte jetzt subscriber fuer die 3 Event-Typen haben.
        # Wir testen das indirekt: publish ein Event und pruefe ob handler
        # called (via _on_response_generated → builder.add_event)
        # Erst Builder oeffnen via Frame:
        frame = _make_frame()
        tracker._manager.state.current_frame = frame
        await bus.publish(FrameUpdated(
            window_title=frame.active_window_title,
            process_name=frame.active_process_name,
            pid=frame.active_pid,
            is_capture_allowed=True,
        ))
        await asyncio.sleep(0.01)    # bus dispatch async
        await bus.publish(ResponseGenerated(text="hello", language="de"))
        await asyncio.sleep(0.01)
        assert tracker._builder is not None
        assert any(
            e["kind"] == "BrainTurnCompleted" for e in tracker._builder.events
        )
    finally:
        await tracker.stop()


async def test_start_idempotent() -> None:
    tracker, *_ = _make_tracker()
    await tracker.start()
    await tracker.start()    # second call must not raise
    await tracker.stop()


async def test_stop_idempotent() -> None:
    tracker, *_ = _make_tracker()
    await tracker.start()
    await tracker.stop()
    await tracker.stop()    # second call must not raise


# ---- Tests: Frame-Filter ---------------------------------------------------


async def test_privacy_blocked_frame_not_buffered() -> None:
    """Hard Negative §6: PrivacyFilter-blockierte Frames NIE in Verdichter-Input."""
    tracker, manager, _, _, _ = _make_tracker()
    manager.state.current_frame = _make_frame(capture_allowed=False)
    ev = FrameUpdated(
        window_title="Banking", process_name="chrome.exe",
        pid=1, is_capture_allowed=False,
    )
    await tracker._on_frame_updated(ev)
    assert tracker._builder is None
    assert tracker._prev_frame is None


async def test_low_salience_frame_dropped() -> None:
    """Frames mit Score < SALIENCE_THRESHOLD landen NICHT im Builder."""
    tracker, manager, _, _, _ = _make_tracker()
    # Boring-Process (Explorer.exe) → -50 Penalty → unter Threshold.
    boring = _make_frame(process="Explorer.exe", title="Datei-Explorer")
    manager.state.current_frame = boring
    ev = FrameUpdated(
        window_title=boring.active_window_title,
        process_name=boring.active_process_name,
        pid=boring.active_pid,
        is_capture_allowed=True,
    )
    await tracker._on_frame_updated(ev)
    # _prev_frame wird trotzdem updated (fuer naechsten Vergleich)
    assert tracker._prev_frame is not None
    # ABER kein Builder geoeffnet
    assert tracker._builder is None


async def test_high_salience_frame_buffered() -> None:
    """High-salience Frame oeffnet Builder + landet drin."""
    tracker, manager, _, _, _ = _make_tracker()
    frame = _make_frame()
    manager.state.current_frame = frame
    ev = FrameUpdated(
        window_title=frame.active_window_title,
        process_name=frame.active_process_name,
        pid=frame.active_pid,
        is_capture_allowed=True,
    )
    await tracker._on_frame_updated(ev)
    assert tracker._builder is not None
    assert tracker._builder.frame_count == 1


# ---- Tests: Trigger-Logik --------------------------------------------------


async def test_app_switch_after_min_duration_triggers_flush() -> None:
    """Process-Wechsel + duration >= 60s → flush via Verdichter."""
    tracker, manager, _, recall, verdichter = _make_tracker(
        story_cfg=AwarenessStoryConfig(episode_min_duration_s=1),
    )
    # Frame 1: Code.exe
    f1 = _make_frame(process="Code.exe", title="main.py", ts_ns=time.time_ns())
    manager.state.current_frame = f1
    await tracker._on_frame_updated(FrameUpdated(
        window_title=f1.active_window_title,
        process_name=f1.active_process_name,
        pid=f1.active_pid, is_capture_allowed=True,
    ))
    # Wait > min_duration (1s in test config)
    await asyncio.sleep(1.1)
    # Frame 2: Chrome.exe (App-Switch)
    f2 = _make_frame(process="Chrome.exe", title="Stack Overflow")
    manager.state.current_frame = f2
    await tracker._on_frame_updated(FrameUpdated(
        window_title=f2.active_window_title,
        process_name=f2.active_process_name,
        pid=f2.active_pid, is_capture_allowed=True,
    ))
    # Verdichter MUSS gerufen worden sein
    assert len(verdichter.calls) == 1
    # Episode persistiert
    assert len(recall.episodes) == 1
    assert recall.episodes[0]["trigger_kind"] == "window_switch"


async def test_short_episode_skipped_on_app_switch() -> None:
    """App-Switch + duration < min → kein Flush, builder reset."""
    tracker, manager, _, recall, verdichter = _make_tracker(
        story_cfg=AwarenessStoryConfig(episode_min_duration_s=60),
    )
    f1 = _make_frame(process="Code.exe")
    manager.state.current_frame = f1
    await tracker._on_frame_updated(FrameUpdated(
        window_title=f1.active_window_title,
        process_name=f1.active_process_name,
        pid=f1.active_pid, is_capture_allowed=True,
    ))
    # KEIN sleep — duration ~0s
    f2 = _make_frame(process="Chrome.exe", title="other")
    manager.state.current_frame = f2
    await tracker._on_frame_updated(FrameUpdated(
        window_title=f2.active_window_title,
        process_name=f2.active_process_name,
        pid=f2.active_pid, is_capture_allowed=True,
    ))
    # Verdichter NICHT gerufen, recall NICHT befuellt
    assert verdichter.calls == []
    assert recall.episodes == []
    # Builder wurde aber reset (nicht None weil neuer Frame eingefuegt)
    # — wir testen: builder.frame_count ist 0 oder builder ist None
    assert tracker._builder is None or tracker._builder.frame_count == 0


async def test_idle_entered_flushes_long_episode() -> None:
    """IdleEntered nach >= min_duration → flush."""
    tracker, manager, _, recall, verdichter = _make_tracker(
        story_cfg=AwarenessStoryConfig(episode_min_duration_s=1),
    )
    f1 = _make_frame()
    manager.state.current_frame = f1
    await tracker._on_frame_updated(FrameUpdated(
        window_title=f1.active_window_title,
        process_name=f1.active_process_name,
        pid=f1.active_pid, is_capture_allowed=True,
    ))
    await asyncio.sleep(1.1)
    await tracker._on_idle_entered(IdleEntered(idle_since_ns=time.time_ns()))
    assert len(verdichter.calls) == 1
    assert recall.episodes[0]["trigger_kind"] == "idle_entered"


async def test_idle_entered_skips_short_episode() -> None:
    """IdleEntered bei zu kurzer Episode → reset, kein Flush."""
    tracker, manager, _, recall, verdichter = _make_tracker(
        story_cfg=AwarenessStoryConfig(episode_min_duration_s=60),
    )
    f1 = _make_frame()
    manager.state.current_frame = f1
    await tracker._on_frame_updated(FrameUpdated(
        window_title=f1.active_window_title,
        process_name=f1.active_process_name,
        pid=f1.active_pid, is_capture_allowed=True,
    ))
    await tracker._on_idle_entered(IdleEntered(idle_since_ns=time.time_ns()))
    assert verdichter.calls == []
    assert recall.episodes == []


async def test_buffer_overflow_forces_flush() -> None:
    """frames > buffer_max → forced flush regardless of duration."""
    tracker, manager, _, recall, verdichter = _make_tracker(
        story_cfg=AwarenessStoryConfig(buffer_max=3, episode_min_duration_s=999),
    )
    # Push 5 distinct frames (different titles → high salience)
    base_ts = time.time_ns()
    for i in range(5):
        # Same process_name (Code.exe), different titles → +30 each (no
        # process-switch trigger; but buffer_max overflow trigger)
        f = _make_frame(
            title=f"file_{i}.py", process="Code.exe",
            ts_ns=base_ts + i * 1_000_000_000,
        )
        manager.state.current_frame = f
        await tracker._on_frame_updated(FrameUpdated(
            window_title=f.active_window_title,
            process_name=f.active_process_name,
            pid=f.active_pid, is_capture_allowed=True,
        ))
    # Buffer-overflow ab Frame 4 → flush mit trigger="buffer_overflow"
    assert len(verdichter.calls) >= 1
    assert any(
        ep["trigger_kind"] == "buffer_overflow" for ep in recall.episodes
    )


# ---- Tests: ResponseGenerated ---------------------------------------------


async def test_response_generated_adds_event_to_builder() -> None:
    """ResponseGenerated → builder.add_event mit kind='BrainTurnCompleted'."""
    tracker, manager, _, _, _ = _make_tracker()
    # Erst Frame, damit Builder existiert
    f = _make_frame()
    manager.state.current_frame = f
    await tracker._on_frame_updated(FrameUpdated(
        window_title=f.active_window_title,
        process_name=f.active_process_name,
        pid=f.active_pid, is_capture_allowed=True,
    ))
    # Jetzt ResponseGenerated
    await tracker._on_response_generated(
        ResponseGenerated(text="Antwort vom Brain", language="de"),
    )
    assert tracker._builder is not None
    events = tracker._builder.events
    assert len(events) == 1
    assert events[0]["kind"] == "BrainTurnCompleted"
    assert events[0]["payload"]["text_len"] == len("Antwort vom Brain")


async def test_response_generated_without_builder_is_noop() -> None:
    """ResponseGenerated ohne offenen Builder → no-op (kein Crash)."""
    tracker, *_ = _make_tracker()
    # Builder ist None
    await tracker._on_response_generated(
        ResponseGenerated(text="hi", language="en"),
    )
    assert tracker._builder is None


# ---- Tests: Persist + State + Event ---------------------------------------


async def test_flush_updates_state_last_episode_summary() -> None:
    """Nach flush: manager.state.last_episode_summary + last_episode_id."""
    tracker, manager, _, recall, verdichter = _make_tracker(
        story_cfg=AwarenessStoryConfig(episode_min_duration_s=1),
        fake_verdichter=FakeVerdichter(summary="Test summary X"),
    )
    f1 = _make_frame()
    manager.state.current_frame = f1
    await tracker._on_frame_updated(FrameUpdated(
        window_title=f1.active_window_title,
        process_name=f1.active_process_name,
        pid=f1.active_pid, is_capture_allowed=True,
    ))
    await asyncio.sleep(1.1)
    await tracker._on_idle_entered(IdleEntered(idle_since_ns=time.time_ns()))
    assert manager.state.last_episode_summary == "Test summary X"
    assert manager.state.last_episode_id == recall.episodes[0]["id"]


async def test_flush_publishes_episode_recorded_event() -> None:
    """Nach flush: bus.publish(EpisodeRecorded(...))."""
    tracker, manager, bus, _, _ = _make_tracker(
        story_cfg=AwarenessStoryConfig(episode_min_duration_s=1),
        fake_verdichter=FakeVerdichter(summary="Hello world"),
    )
    received: list[EpisodeRecorded] = []

    async def collect(ev: EpisodeRecorded) -> None:
        received.append(ev)

    bus.subscribe(EpisodeRecorded, collect)

    f = _make_frame()
    manager.state.current_frame = f
    await tracker._on_frame_updated(FrameUpdated(
        window_title=f.active_window_title,
        process_name=f.active_process_name,
        pid=f.active_pid, is_capture_allowed=True,
    ))
    await asyncio.sleep(1.1)
    await tracker._on_idle_entered(IdleEntered(idle_since_ns=time.time_ns()))
    await asyncio.sleep(0.05)    # bus dispatch
    assert len(received) == 1
    assert received[0].summary_preview == "Hello world"
    assert received[0].frame_count == 1


# ---- Tests: Failure-Modes -------------------------------------------------


async def test_verdichter_exception_handled_gracefully() -> None:
    """Verdichter wirft → empty summary, Episode trotzdem persistiert."""
    tracker, manager, _, recall, _ = _make_tracker(
        story_cfg=AwarenessStoryConfig(episode_min_duration_s=1),
        fake_verdichter=FakeVerdichter(raise_exc=RuntimeError("brain dead")),
    )
    f = _make_frame()
    manager.state.current_frame = f
    await tracker._on_frame_updated(FrameUpdated(
        window_title=f.active_window_title,
        process_name=f.active_process_name,
        pid=f.active_pid, is_capture_allowed=True,
    ))
    await asyncio.sleep(1.1)
    await tracker._on_idle_entered(IdleEntered(idle_since_ns=time.time_ns()))
    # Episode wurde persistiert MIT empty summary
    assert len(recall.episodes) == 1
    assert recall.episodes[0]["summary"] == ""
    assert recall.episodes[0]["tokens_in"] == 0


async def test_recall_exception_drops_episode_no_crash() -> None:
    """recall.record_episode wirft → episode lost, kein Crash, no state-update."""
    tracker, manager, _, recall, _ = _make_tracker(
        story_cfg=AwarenessStoryConfig(episode_min_duration_s=1),
        fake_recall=FakeRecall(raise_on_record=True),
    )
    f = _make_frame()
    manager.state.current_frame = f
    await tracker._on_frame_updated(FrameUpdated(
        window_title=f.active_window_title,
        process_name=f.active_process_name,
        pid=f.active_pid, is_capture_allowed=True,
    ))
    await asyncio.sleep(1.1)
    await tracker._on_idle_entered(IdleEntered(idle_since_ns=time.time_ns()))
    # State NICHT updated weil persist failed
    assert manager.state.last_episode_summary == ""
    assert recall.episodes == []


async def test_stop_force_flushes_remaining_builder() -> None:
    """stop() flush ungeachtet von duration (trigger='stop' zwingt flush)."""
    tracker, manager, _, recall, verdichter = _make_tracker(
        story_cfg=AwarenessStoryConfig(episode_min_duration_s=999),
    )
    await tracker.start()
    f = _make_frame()
    manager.state.current_frame = f
    await tracker._on_frame_updated(FrameUpdated(
        window_title=f.active_window_title,
        process_name=f.active_process_name,
        pid=f.active_pid, is_capture_allowed=True,
    ))
    # KEIN sleep — duration ~0s. Aber stop forciert flush.
    await tracker.stop()
    assert len(verdichter.calls) == 1
    assert recall.episodes[0]["trigger_kind"] == "stop"


async def test_stale_state_blocked_frame_does_not_leak(
) -> None:
    """M2-Fix: blocked Frame in state.current_frame darf NIE im Builder landen,
    auch wenn das Event als allowed kam (Race-Window).
    """
    tracker, manager, _, _, _ = _make_tracker()
    # Setup: state hat blocked Frame, Event sagt aber allowed=True
    blocked_frame = _make_frame(
        title="Banking", process="chrome.exe",
        capture_allowed=False,
    )
    manager.state.current_frame = blocked_frame
    ev = FrameUpdated(
        window_title="something_else", process_name="Code.exe",
        pid=1, is_capture_allowed=True,    # Event sagt allowed
    )
    await tracker._on_frame_updated(ev)
    # M2-Defense: re-check stops the leak
    assert tracker._builder is None


async def test_hard_timer_path_persists_episode_with_correct_trigger() -> None:
    """AC-3 Trigger-Path: _maybe_flush(trigger_kind='hard_timer') persistiert
    Episode mit trigger_kind='hard_timer'. Wir simulieren den Timer-Tick
    direkt statt 5min real-time zu warten.
    """
    tracker, manager, _, recall, _ = _make_tracker(
        story_cfg=AwarenessStoryConfig(episode_min_duration_s=1),
    )
    base_ns = time.time_ns() - 2_000_000_000    # 2s in der Vergangenheit
    f = _make_frame(ts_ns=base_ns)
    manager.state.current_frame = f
    await tracker._on_frame_updated(FrameUpdated(
        window_title=f.active_window_title,
        process_name=f.active_process_name,
        pid=f.active_pid, is_capture_allowed=True,
    ))
    # B1-Refactor: _maybe_flush nimmt seinen Lock selbst — Caller darf
    # NICHT mehr den Lock halten (sonst Deadlock auf asyncio.Lock).
    await tracker._maybe_flush(trigger_kind="hard_timer")
    assert recall.episodes[0]["trigger_kind"] == "hard_timer"


async def test_hard_timer_loop_lifecycle_starts_and_cancels_cleanly() -> None:
    """AC-3 Lifecycle: _hard_timer_loop laeuft im Background-Task und cancelt
    sauber bei stop() — kein hanging Task, kein Resource-Leak.
    """
    tracker, *_ = _make_tracker()
    assert tracker._timer_task is None
    await tracker.start()
    assert tracker._timer_task is not None
    assert not tracker._timer_task.done()
    await tracker.stop()
    assert tracker._timer_task is None    # nach stop ist Task abgeraeumt


async def test_max_duration_triggers_flush() -> None:
    """m4-Fix: Episode > episode_max_duration_min → forced flush.

    Single-Frame-Setup: ts_ns liegt 2min in der Vergangenheit, sodass die
    Builder.duration_ns gleich beim ersten add_frame > max_duration_min*60s
    ist. Damit haben wir genau 1 Verdichter-Call (deterministisch).
    """
    tracker, manager, _, recall, verdichter = _make_tracker(
        story_cfg=AwarenessStoryConfig(
            episode_min_duration_s=999,    # min = high, sonst Flush via min
            episode_max_duration_min=1,    # max = 1min fuer Test
            buffer_max=999,                # buffer wide enough
        ),
    )
    base_ns = time.time_ns() - (2 * 60 * 1_000_000_000)    # 2min in der Vergangenheit
    f = _make_frame(title="long_session.py", ts_ns=base_ns)
    manager.state.current_frame = f
    await tracker._on_frame_updated(FrameUpdated(
        window_title=f.active_window_title,
        process_name=f.active_process_name,
        pid=f.active_pid, is_capture_allowed=True,
    ))
    # max_duration trigger feuert direkt nach add_frame —
    # Builder.duration_ns ≈ 2min > 1min Hard-Cap.
    assert len(verdichter.calls) == 1
    assert recall.episodes[0]["trigger_kind"] == "max_duration"


# ---- Tests: Concurrency (Codex-BLOCKER B1 + B2 Fix, 2026-05-11) ------------


@dataclass
class SlowVerdichter:
    """Fake-Verdichter, der seinen Call ``sleep_s`` blockiert.

    Genutzt um die B1-Lock-Holding-Pattern zu reproduzieren — ohne den
    B1-Fix wuerde der Lock 5s gehalten und parallele _on_frame_updated
    serialisiert ausfuehren.
    """
    sleep_s: float = 0.5
    summary: str = "slow summary"
    calls_started: int = 0
    calls_completed: int = 0

    async def call(
        self,
        *,
        frames: list[dict[str, Any]],
        events: list[dict[str, Any]],
        primary_app: str,
    ) -> tuple[str, dict[str, Any]]:
        self.calls_started += 1
        await asyncio.sleep(self.sleep_s)
        self.calls_completed += 1
        return self.summary, {
            "tokens_in": 10, "tokens_out": 5,
            "duration_ms": int(self.sleep_s * 1000),
        }


async def test_b1_lock_free_during_verdichter_call() -> None:
    """B1-Regression (AC-strict): Mock-Verdichter blockt 5s, 50 parallele
    Frame-Pushes — Throughput im Test < 100ms gesamt (nicht 5s seriell).

    Plan-AC (JARVIS_AWARENESS_PLAN §6 Folge-AC, Codex-BLOCKER B1):
    - Verdichter sleep = 5.0s (harter AC-Wert aus dem Spike-Prompt).
    - 50 parallele Pushes muessen in <0.1s (100ms) durch sein. Ohne den
      B1-Fix wuerde Push #1 die ganzen 5s auf dem Lock warten; alle 50
      seriell waeren ~ 5s * 50 = 250s.
    - Mit B1-Fix wird der Lock VOR dem 5s sleep released — die 50 Pushes
      laufen in <100ms durch.
    """
    slow = SlowVerdichter(sleep_s=5.0)
    tracker, manager, _, _, _ = _make_tracker(
        story_cfg=AwarenessStoryConfig(episode_min_duration_s=1),
        fake_verdichter=slow,    # type: ignore[arg-type]
    )

    f1 = _make_frame(process="Code.exe", title="orig.py")
    manager.state.current_frame = f1
    await tracker._on_frame_updated(FrameUpdated(
        window_title=f1.active_window_title,
        process_name=f1.active_process_name,
        pid=f1.active_pid, is_capture_allowed=True,
    ))
    await asyncio.sleep(1.1)

    # Idle → triggers slow flush in background.
    flush_task = asyncio.create_task(
        tracker._on_idle_entered(IdleEntered(idle_since_ns=time.time_ns())),
    )
    # Yield until the flush task has actually entered verdichter.call. We
    # poll calls_started rather than blindly spinning asyncio.sleep(0) so
    # this is not flaky on slow CI.
    for _ in range(100):
        await asyncio.sleep(0)
        if slow.calls_started >= 1:
            break
    assert slow.calls_started == 1, (
        "Pre-condition: verdichter must have entered its sleep"
    )

    # Hammer 50 frame updates while verdichter is blocked.
    start = time.monotonic()
    for i in range(50):
        f = _make_frame(process="Code.exe", title=f"hammer_{i}.py")
        manager.state.current_frame = f
        await tracker._on_frame_updated(FrameUpdated(
            window_title=f.active_window_title,
            process_name=f.active_process_name,
            pid=f.active_pid, is_capture_allowed=True,
        ))
    elapsed = time.monotonic() - start

    # AC-strict: 50 pushes in <100ms while verdichter still sleeps. Without
    # the B1-fix Push #1 alone would wait 5s on the lock — alle 50 zusammen
    # haetten 250s+ gedauert.
    assert elapsed < 0.1, (
        f"B1-Regression: {elapsed:.3f}s for 50 frame updates while verdichter "
        f"blocks 5s (AC: <0.1s — Lock is held over verdichter.call)"
    )

    # Flush task must still complete cleanly (verdichter sleep = 5s + slack).
    await asyncio.wait_for(flush_task, timeout=6.5)
    assert slow.calls_completed == 1


async def test_b1_idle_handler_does_not_block_frame_handler() -> None:
    """B1: idle-flush + frame-update interleaved → frame-update returnt sofort.

    Direkter Stress-Test: idle-handler haelt internen run_flush busy via
    SlowVerdichter; ein paralleler frame-update darf nicht warten.
    """
    slow = SlowVerdichter(sleep_s=0.4)
    tracker, manager, _, _, _ = _make_tracker(
        story_cfg=AwarenessStoryConfig(episode_min_duration_s=1),
        fake_verdichter=slow,    # type: ignore[arg-type]
    )
    f1 = _make_frame(process="Code.exe", title="initial.py")
    manager.state.current_frame = f1
    await tracker._on_frame_updated(FrameUpdated(
        window_title=f1.active_window_title,
        process_name=f1.active_process_name,
        pid=f1.active_pid, is_capture_allowed=True,
    ))
    await asyncio.sleep(1.1)

    idle_task = asyncio.create_task(
        tracker._on_idle_entered(IdleEntered(idle_since_ns=time.time_ns())),
    )
    # Let idle_task grab+release lock and start verdichter.
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    f2 = _make_frame(process="Code.exe", title="parallel.py", ts_ns=time.time_ns())
    manager.state.current_frame = f2
    start = time.monotonic()
    await tracker._on_frame_updated(FrameUpdated(
        window_title=f2.active_window_title,
        process_name=f2.active_process_name,
        pid=f2.active_pid, is_capture_allowed=True,
    ))
    elapsed = time.monotonic() - start

    # Frame handler MUST not have waited for the 0.4s verdichter — Lock
    # was released before run_flush (B1).
    assert elapsed < 0.1, (
        f"B1-Regression: frame handler took {elapsed:.3f}s while idle-flush "
        f"was holding lock (must be <0.1s)"
    )
    await asyncio.wait_for(idle_task, timeout=2.0)


async def test_b2_no_frame_loss_during_concurrent_flush() -> None:
    """B2-Regression: Frame, der waehrend Flush gepusht wird, MUSS in
    irgendeiner Episode landen (alte oder naechste) — niemals "lost".

    Setup: Frame 1 öffnet Builder, Idle triggert Flush (Verdichter blockt
    300ms). Während des Flush wird ein zweiter Frame gepusht. Wir
    erwarten:
    - Episode 1 enthaelt Frame 1 (frame_count == 1).
    - Frame 2 startet Episode 2 (neuer Builder, da _builder=None nach
      atomic detach im B1-Snapshot).
    - Final-Flush via stop() persistiert Episode 2 mit Frame 2.
    """
    slow = SlowVerdichter(sleep_s=0.3, summary="ep1")
    tracker, manager, _, recall, _ = _make_tracker(
        story_cfg=AwarenessStoryConfig(episode_min_duration_s=1),
        fake_verdichter=slow,    # type: ignore[arg-type]
    )
    await tracker.start()

    try:
        f1 = _make_frame(process="Code.exe", title="ep1.py")
        manager.state.current_frame = f1
        await tracker._on_frame_updated(FrameUpdated(
            window_title=f1.active_window_title,
            process_name=f1.active_process_name,
            pid=f1.active_pid, is_capture_allowed=True,
        ))
        await asyncio.sleep(1.1)

        # Trigger flush in background (verdichter blocks 300ms).
        flush_task = asyncio.create_task(
            tracker._on_idle_entered(IdleEntered(idle_since_ns=time.time_ns())),
        )
        # Let the flush task acquire+release the lock and reach verdichter.call.
        await asyncio.sleep(0)
        await asyncio.sleep(0.05)

        # Push frame 2 while verdichter is still mid-flight. WITHOUT B2-fix
        # this could land in the dying-builder buffer (lost when
        # _builder=None is set without atomic detach) or in a fresh builder
        # that gets immediately overwritten. With B2-fix the snapshot+reset
        # is atomic, so frame 2 goes into a brand-new empty builder.
        f2 = _make_frame(
            process="Code.exe",
            title="ep2.py",
            ts_ns=time.time_ns(),
        )
        manager.state.current_frame = f2
        await tracker._on_frame_updated(FrameUpdated(
            window_title=f2.active_window_title,
            process_name=f2.active_process_name,
            pid=f2.active_pid, is_capture_allowed=True,
        ))

        # Wait for the in-flight flush to complete.
        await asyncio.wait_for(flush_task, timeout=2.0)

        # Episode 1 persisted with exactly frame_count == 1 (only f1).
        assert len(recall.episodes) == 1
        assert recall.episodes[0]["summary"] == "ep1"
        assert recall.episodes[0]["frame_count"] == 1

        # Frame 2 must be in the live builder (not lost).
        assert tracker._builder is not None
        live_titles = [f.active_window_title for f in tracker._builder.frames]
        assert "ep2.py" in live_titles, (
            f"B2-Regression: frame ep2.py disappeared. Live builder has "
            f"{live_titles!r}"
        )
    finally:
        # Force-flush ep2 via stop() to verify persistence.
        await tracker.stop()

    # Episode 2 from stop()-trigger persisted with frame 2.
    assert len(recall.episodes) == 2
    assert recall.episodes[1]["trigger_kind"] == "stop"
    assert recall.episodes[1]["frame_count"] == 1


async def test_b2_extract_snapshot_locked_resets_builder() -> None:
    """B2: nach _extract_snapshot_locked ist der Builder None und alle
    Frames/Events sind im Snapshot — kein Doppel-Bookkeeping.
    """
    tracker, manager, _, _, _ = _make_tracker(
        story_cfg=AwarenessStoryConfig(episode_min_duration_s=1),
    )
    f = _make_frame(title="x.py", ts_ns=time.time_ns() - 2_000_000_000)
    manager.state.current_frame = f
    await tracker._on_frame_updated(FrameUpdated(
        window_title=f.active_window_title,
        process_name=f.active_process_name,
        pid=f.active_pid, is_capture_allowed=True,
    ))
    assert tracker._builder is not None
    assert tracker._builder.frame_count == 1

    async with tracker._lock:
        snap = tracker._extract_snapshot_locked(trigger_kind="window_switch")

    assert snap is not None
    assert tracker._builder is None    # B2: builder reset atomar
    assert len(snap.frames) == 1
    assert snap.frames[0]["window_title"] == "x.py"
    assert snap.primary_app == f.active_process_name
