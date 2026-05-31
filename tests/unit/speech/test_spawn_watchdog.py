"""CRIT-5 (User-Wahl 2026-05-17): spawn-watchdog tests.

When the user fires a force-spawn-worker, the worker runs silently for
the duration of the mission. Per the 2026-05-12 calibration, the
Spawn-ACK is suppressed -- but Audit-1 found the resulting 40-90 s
silence leaves the user unable to tell whether Jarvis is working or
stuck. The user chose Watchdog (90 s threshold) on 2026-05-17.

These tests pin the contract:
  * spawn-announcement schedules a watchdog
  * background-completed cancels it (no "Bin noch dran." in the happy path)
  * watchdog firing emits the discrete progress phrase
  * mute suppresses the phrase even if the watchdog fires
  * multiple parallel spawns each get their own watchdog (FIFO cancel)
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.events import (
    AnnouncementRequested,
    OpenClawAnnouncement,
    OpenClawBackgroundCompleted,
    VoiceMuteToggleRequested,
)
from jarvis.core.protocols import AudioChunk
from jarvis.speech.pipeline import SpeechPipeline


@dataclass
class FakeTTS:
    name: str = "fake-tts"
    supports_streaming: bool = True
    calls: list[tuple[str, str | None]] = field(default_factory=list)

    async def synthesize(
        self, text: str, voice: str | None = None,
        language_code: str | None = None,
    ) -> AsyncIterator[AudioChunk]:
        self.calls.append((text, language_code))
        if False:  # pragma: no cover
            yield  # type: ignore[unreachable]


@dataclass
class FakePlayer:
    plays: list[str] = field(default_factory=list)

    async def play_chunks(self, chunks: AsyncIterator[AudioChunk]) -> None:
        self.plays.append("play")
        async for _ in chunks:
            pass

    def stop(self) -> None:
        return None


def _pipeline(bus: EventBus, *, watchdog_delay_s: float) -> SpeechPipeline:
    """Build a SpeechPipeline with the watchdog delay overridden so the
    tests don't have to wait 90 real seconds. ``enable_whisper_wake``
    keeps the heavy Whisper bootstrap out of the test path."""
    tts = FakeTTS()
    pipe = SpeechPipeline(tts=tts, bus=bus, enable_whisper_wake=False)
    pipe._player = FakePlayer()  # type: ignore[assignment]
    pipe._spawn_watchdog_delay_s = watchdog_delay_s
    return pipe


@pytest.mark.asyncio
async def test_completion_within_window_cancels_watchdog() -> None:
    """Happy path: mission completes quickly -> watchdog cancelled ->
    no progress phrase emitted."""
    bus = EventBus()
    pipe = _pipeline(bus, watchdog_delay_s=10.0)

    announcements: list[AnnouncementRequested] = []
    bus.subscribe(AnnouncementRequested, lambda ev: announcements.append(ev))

    await bus.publish(OpenClawAnnouncement(action="bauen", target="x"))
    await asyncio.sleep(0.02)  # let the watchdog task get scheduled

    await bus.publish(OpenClawBackgroundCompleted(success=True, summary="ok"))
    await asyncio.sleep(0.05)

    progress = [a for a in announcements if a.text == "Bin noch dran."]
    assert progress == [], (
        "watchdog must NOT fire when completion arrives within the window"
    )


@pytest.mark.asyncio
async def test_no_completion_triggers_watchdog_phrase() -> None:
    """Long-running mission: no completion within the watchdog window
    -> one discrete 'Bin noch dran.' announcement reaches the bus."""
    bus = EventBus()
    # Tiny delay -- 50 ms is plenty for the asyncio.sleep to expire.
    pipe = _pipeline(bus, watchdog_delay_s=0.05)

    announcements: list[AnnouncementRequested] = []
    bus.subscribe(AnnouncementRequested, lambda ev: announcements.append(ev))

    await bus.publish(OpenClawAnnouncement(action="bauen", target="x"))
    # Wait long enough for the watchdog timer to fire.
    await asyncio.sleep(0.3)

    progress = [a for a in announcements if a.text == "Bin noch dran."]
    assert len(progress) == 1, (
        f"watchdog must emit exactly one progress phrase, got {progress!r}"
    )
    assert progress[0].language == "de"
    assert progress[0].priority == "normal"


@pytest.mark.asyncio
async def test_watchdog_respects_mute() -> None:
    """If the user toggled mute, the watchdog must NOT publish the
    progress phrase even when the timer fires. The 2026-05-17 trade-off
    is 'one discrete phrase' -- it must not become 'phrase even when
    user explicitly silenced everything'."""
    bus = EventBus()
    pipe = _pipeline(bus, watchdog_delay_s=0.05)

    announcements: list[AnnouncementRequested] = []
    bus.subscribe(AnnouncementRequested, lambda ev: announcements.append(ev))

    # Mute first -- the wake/announcement path is now globally silent.
    await bus.publish(VoiceMuteToggleRequested(source="test"))
    assert pipe.is_muted is True

    await bus.publish(OpenClawAnnouncement(action="bauen", target="x"))
    await asyncio.sleep(0.3)

    progress = [a for a in announcements if a.text == "Bin noch dran."]
    assert progress == [], (
        "watchdog must be muted along with the rest of voice output"
    )


@pytest.mark.asyncio
async def test_multiple_spawns_each_get_a_watchdog() -> None:
    """If two spawns happen in quick succession (sequential dispatch),
    each gets its own watchdog and FIFO completion cancels the older
    one first. The newer watchdog still fires if its mission stays
    open."""
    bus = EventBus()
    pipe = _pipeline(bus, watchdog_delay_s=0.05)

    announcements: list[AnnouncementRequested] = []
    bus.subscribe(AnnouncementRequested, lambda ev: announcements.append(ev))

    await bus.publish(OpenClawAnnouncement(action="A", target="a"))
    await bus.publish(OpenClawAnnouncement(action="B", target="b"))
    assert len(pipe._spawn_watchdog_tasks) == 2

    # Complete only the first spawn -- the second's watchdog stays alive.
    await bus.publish(OpenClawBackgroundCompleted(success=True, summary="A done"))
    await asyncio.sleep(0.3)

    progress = [a for a in announcements if a.text == "Bin noch dran."]
    assert len(progress) == 1, (
        "FIFO cancel must spare the newer watchdog, which then fires"
    )


@pytest.mark.asyncio
async def test_finish_after_response_stays_listening_while_spawn_in_flight() -> None:
    """BUG: when a force-spawn-worker fires and single-turn-mode is on,
    ``_finish_after_response`` used to hang up immediately after the spawn ACK
    -- the mic context exited, the user could not follow up, and the eventual
    background-completed readback played 'into the void'.

    The fix re-uses the existing ``_spawn_watchdog_tasks`` FIFO as the
    canonical 'spawns in flight' tracker: as long as at least one spawn
    has not yet emitted ``OpenClawBackgroundCompleted``, the turn is not
    semantically complete and the pipeline must stay LISTENING.
    """
    from jarvis.speech.pipeline import TurnTakingState

    bus = EventBus()
    pipe = _pipeline(bus, watchdog_delay_s=60.0)
    # Single-turn-mode: without the fix, _finish_after_response hangs up.
    pipe._continue_listening_after_response = False

    await bus.publish(OpenClawAnnouncement(action="bauen", target="x"))
    await asyncio.sleep(0.02)
    assert len(pipe._spawn_watchdog_tasks) == 1, "spawn must be tracked"

    # Simulate the post-ACK code path: response delivered, now decide whether
    # to hang up. With a spawn in flight the answer is "no, keep listening".
    result = await pipe._finish_after_response(barged=False)
    assert result is True, (
        "Pipeline must stay alive (return True) while a spawn is in flight "
        "-- otherwise the mic closes and the background readback plays into "
        "a dead session."
    )
    assert pipe._turn_state == TurnTakingState.LISTENING, (
        "State must transition back to LISTENING, not IDLE, while waiting "
        "for the mission readback."
    )
    assert pipe._session_end_reason is None, (
        "_session_end_reason must NOT be set to HANGUP_TURN_COMPLETE -- the "
        "turn is not complete until OpenClawBackgroundCompleted fires."
    )

    # After the mission completes the watchdog is popped; a follow-up turn
    # then respects single-turn-mode again.
    await bus.publish(OpenClawBackgroundCompleted(success=True, summary="ok"))
    await asyncio.sleep(0.05)
    assert pipe._spawn_watchdog_tasks == []

    result = await pipe._finish_after_response(barged=False)
    assert result is False, (
        "With no more spawns in flight, single-turn-mode must hang up again."
    )
    assert pipe._session_end_reason is not None


@pytest.mark.asyncio
async def test_completion_after_watchdog_fires_is_still_clean() -> None:
    """If the watchdog has already fired and the mission then finishes,
    the completion path must not crash on the already-done task -- the
    cancel must be a no-op for a completed task."""
    bus = EventBus()
    pipe = _pipeline(bus, watchdog_delay_s=0.02)

    await bus.publish(OpenClawAnnouncement(action="x", target="y"))
    await asyncio.sleep(0.2)  # watchdog fires
    # Completing afterwards must not raise.
    await bus.publish(OpenClawBackgroundCompleted(success=True, summary="ok"))
    await asyncio.sleep(0.05)
    assert pipe._spawn_watchdog_tasks == []


@pytest.mark.asyncio
async def test_fired_watchdog_self_removes_from_inflight_list() -> None:
    """Bound for the idle-timeout / finish-after-response override.

    In production a *successful* background mission never publishes
    ``OpenClawBackgroundCompleted`` — the readback travels the MissionAnnouncer
    path (MissionApproved → AnnouncementRequested), and ``_on_background_completed``
    (the only code that pops ``_spawn_watchdog_tasks``) fires solely on the crash
    path. So the watchdog is never *cancelled*; it simply *fires* after the delay.

    A fired watchdog MUST drop itself from ``_spawn_watchdog_tasks`` (via its own
    self-removal, surfaced through ``_live_spawn_watchdogs``); otherwise a
    done-but-listed task keeps the voice session open forever.
    """
    bus = EventBus()
    pipe = _pipeline(bus, watchdog_delay_s=0.05)

    await bus.publish(OpenClawAnnouncement(action="bauen", target="x"))
    await asyncio.sleep(0.02)
    assert len(pipe._spawn_watchdog_tasks) == 1, "spawn must arm a watchdog"

    # Let the watchdog fire its single progress phrase. NO completion event is
    # published — this is the production success path.
    await asyncio.sleep(0.25)

    assert pipe._live_spawn_watchdogs() == [], (
        "a fired watchdog must self-remove; otherwise the done task lingers and "
        "the idle-timeout override keeps the session listening forever"
    )


@pytest.mark.asyncio
async def test_finish_after_response_hangs_up_once_watchdog_has_fired() -> None:
    """Consumer-level proof of the "not forever" bound.

    Single-turn mode: while a mission is genuinely in flight (watchdog still
    counting down) ``_finish_after_response`` keeps the turn open. Once the
    watchdog has fired — the mission had its full grace window and the user was
    reassured with "Bin noch dran." — a leaked done-task must NOT keep the turn
    open. Pre-fix the done task lingered and ``_finish_after_response`` returned
    ``True`` (keep listening) on every subsequent turn forever.
    """
    bus = EventBus()
    pipe = _pipeline(bus, watchdog_delay_s=0.05)
    pipe._continue_listening_after_response = False  # single-turn mode

    await bus.publish(OpenClawAnnouncement(action="bauen", target="x"))
    await asyncio.sleep(0.02)
    assert await pipe._finish_after_response(barged=False) is True  # in flight

    await asyncio.sleep(0.25)  # watchdog fires (no completion event ever arrives)
    assert await pipe._finish_after_response(barged=False) is False, (
        "with the watchdog fired and no live mission, single-turn mode must "
        "hang up again instead of listening forever"
    )
