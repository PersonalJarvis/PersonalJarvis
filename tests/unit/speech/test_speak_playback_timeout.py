"""``_speak`` must never hang on a stalled audio device / TTS stream.

Live incident (2026-06-01): a flaky output device made PortAudio's blocking
``stream.write`` (and once the TTS chunk generator) wedge ``play_chunks``
forever. ``_speak`` had no timeout around playback, so it never returned. That
froze ``_handle_utterance`` → ``_active_session``, so the ``_state_loop``
``finally`` that resets ``self._state`` to ``IDLE`` (the wake-loop's re-arm
gate) never ran — and "Hey Jarvis" went permanently deaf until a restart.

These tests pin the contract: regardless of which part of playback stalls,
``_speak`` returns within the hard ceiling and aborts the player (AD-OE6 —
recover, never silently hang).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.protocols import AudioChunk
from jarvis.speech.pipeline import SpeechPipeline


@dataclass
class FakeTTS:
    name: str = "fake-tts"
    supports_streaming: bool = True
    calls: list[tuple[str, str | None]] = field(default_factory=list)

    async def synthesize(
        self,
        text: str,
        voice: str | None = None,
        language_code: str | None = None,
    ) -> AsyncIterator[AudioChunk]:
        self.calls.append((text, language_code))
        if False:  # pragma: no cover - empty async generator
            yield  # type: ignore[unreachable]


@dataclass
class HangingPlayer:
    """A player whose ``play_chunks`` never completes on its own.

    Models the live failure mode: PortAudio's blocking ``stream.write`` (or a
    stalled TTS chunk generator) parks ``play_chunks`` indefinitely. ``stop()``
    is the only thing that releases it — exactly what ``_speak`` must invoke on
    a ceiling breach.
    """

    stop_calls: int = 0
    _release: asyncio.Event = field(default_factory=asyncio.Event)

    async def play_chunks(self, chunks: AsyncIterator[AudioChunk]) -> None:
        # Drain whatever was handed in, then block until stop() releases us.
        async for _ in chunks:
            pass
        await self._release.wait()

    def stop(self) -> None:
        self.stop_calls += 1
        self._release.set()


def _make_pipeline() -> tuple[SpeechPipeline, HangingPlayer]:
    bus = EventBus()
    pipeline = SpeechPipeline(tts=FakeTTS(), bus=bus, enable_whisper_wake=False)
    player = HangingPlayer()
    pipeline._player = player  # type: ignore[assignment]
    # Tiny ceiling so the test is fast; the real default is generous.
    pipeline._speak_playback_ceiling_s = 0.2  # type: ignore[attr-defined]
    return pipeline, player


@pytest.mark.asyncio
async def test_speak_returns_when_playback_stalls_and_barge_idles() -> None:
    """Barge monitor idles (returns False); playback wedges → ceiling aborts."""
    pipeline, player = _make_pipeline()

    async def _no_barge() -> bool:
        return False

    pipeline._barge_monitor = _no_barge  # type: ignore[assignment,method-assign]

    # If the ceiling is missing, _speak hangs forever and this wait_for raises.
    barged = await asyncio.wait_for(pipeline._speak("hallo", language="de"), timeout=5.0)

    assert barged is False
    assert player.stop_calls >= 1, "stalled playback must be aborted via stop()"


@pytest.mark.asyncio
async def test_speak_returns_when_both_playback_and_barge_stall() -> None:
    """Barge monitor never returns either → main-wait ceiling aborts playback."""
    pipeline, player = _make_pipeline()

    async def _hang_barge() -> bool:
        await asyncio.Event().wait()
        return False  # pragma: no cover - never reached

    pipeline._barge_monitor = _hang_barge  # type: ignore[assignment,method-assign]

    barged = await asyncio.wait_for(pipeline._speak("hallo", language="de"), timeout=5.0)

    assert barged is False
    assert player.stop_calls >= 1, "stalled playback must be aborted via stop()"


async def _empty_chunks() -> AsyncIterator[AudioChunk]:
    return
    yield  # pragma: no cover - makes this an async generator


@dataclass
class ProgressingPlayer:
    """A HEALTHY long playback that keeps writing frames past the ceiling.

    Models a legitimately long spoken answer (e.g. reading a summary): it makes
    continuous write-progress for ``play_duration_s``. The watchdog must NOT
    abort it just because total time crossed the (pre-first-frame) ceiling — the
    flat 20 s ceiling used to truncate any answer longer than 20 s.
    """

    play_duration_s: float = 0.8
    last_write_ns: int = 0
    aborted: bool = False

    async def play_chunks(self, chunks: AsyncIterator[AudioChunk]) -> None:
        self.last_write_ns = 0  # per-playback reset (the Part-A behaviour)
        async for _ in chunks:
            pass
        steps = max(1, int(self.play_duration_s / 0.05))
        for _ in range(steps):  # frames keep flowing across the ceiling
            if self.aborted:
                break
            await asyncio.sleep(0.05)
            self.last_write_ns = time.monotonic_ns()

    def abort_active(self) -> None:
        self.aborted = True

    def stop(self) -> None:
        self.aborted = True


@pytest.mark.asyncio
async def test_await_playback_does_not_abort_long_active_playback() -> None:
    """A healthy, actively-progressing playback must survive past the ceiling.

    Regression for the watchdog redesign: the old flat total-time ceiling aborted
    ANY single spoken turn longer than the ceiling, even while frames were still
    flowing. The ceiling now only bounds the no-first-frame window; an active
    playback is governed solely by the mid-playback no-progress stall.
    """
    bus = EventBus()
    pipeline = SpeechPipeline(tts=FakeTTS(), bus=bus, enable_whisper_wake=False)
    player = ProgressingPlayer(play_duration_s=0.8)
    pipeline._player = player  # type: ignore[assignment]
    # Ceiling far SHORTER than the playback, stall window long enough to never
    # trip (progress every 50 ms). Before the fix the ceiling aborts at 0.3 s.
    pipeline._speak_playback_ceiling_s = 0.3  # type: ignore[attr-defined]
    pipeline._speak_playback_stall_s = 5.0  # type: ignore[attr-defined]

    play_task = asyncio.create_task(player.play_chunks(_empty_chunks()))
    done = await asyncio.wait_for(
        pipeline._await_playback(play_task, set()), timeout=5.0
    )

    assert done == {play_task}, "healthy long playback must not be aborted by the ceiling"
    assert player.aborted is False


@dataclass
class WedgeAfterFirstFramePlayer:
    """A device that writes one frame, then the blocking write wedges forever.

    This is the ORIGINAL Wave-1 failure the watchdog exists for: frames started
    flowing, then ``stream.write`` froze. The mid-playback no-progress stall must
    still abort it — the watchdog redesign must not weaken that protection.
    """

    last_write_ns: int = 0
    aborted: bool = False
    _released: asyncio.Event = field(default_factory=asyncio.Event)

    async def play_chunks(self, chunks: AsyncIterator[AudioChunk]) -> None:
        async for _ in chunks:
            pass
        self.last_write_ns = time.monotonic_ns()  # first frame written...
        await self._released.wait()  # ...then wedged: no more progress until abort

    def abort_active(self) -> None:
        self.aborted = True
        self._released.set()

    def stop(self) -> None:
        self.aborted = True
        self._released.set()


@pytest.mark.asyncio
async def test_no_first_frame_ceiling_abort_marks_beheaded_turn() -> None:
    """The ceiling abort must leave a per-turn mark
    (``_playback_aborted_no_first_frame``) so the empty-turn handler can speak
    an audible timeout notice instead of dropping to silent LISTENING (live
    bug 2026-06-10 14:34)."""
    bus = EventBus()
    pipeline = SpeechPipeline(tts=FakeTTS(), bus=bus, enable_whisper_wake=False)
    player = HangingPlayer()  # never writes a frame
    pipeline._player = player  # type: ignore[assignment]
    pipeline._speak_playback_ceiling_s = 0.2  # type: ignore[attr-defined]

    play_task = asyncio.create_task(player.play_chunks(_empty_chunks()))
    done = await asyncio.wait_for(
        pipeline._await_playback(play_task, set()), timeout=5.0
    )

    assert done == set()
    assert getattr(pipeline, "_playback_aborted_no_first_frame", False) is True
    if not play_task.done():  # the abort released the player; tidy up anyway
        play_task.cancel()


@pytest.mark.asyncio
async def test_no_first_frame_ceiling_deferred_while_desktop_tool_steps() -> None:
    """An actively-stepping computer_use turn must not be beheaded pre-first-frame.

    Live bug 2026-06-09 (data/jarvis_desktop.log 19:46, "öffne CapCut"): the
    router brain ran an inline ``computer_use`` loop. The loop was working on
    step 4 (heartbeats flowing via ObservationCaptured/ActionPlanned →
    ``_on_agent_progress``) when the no-first-frame ceiling fired at 20 s,
    aborted the device, unwound the streaming turn, and the answer came back
    EMPTY — silence (or, earlier, the canned clarify phrase) instead of a real
    result. The brain stall guard already suspends its ceiling on these
    heartbeats; ``_await_playback`` must honour the same liveness signal.
    """
    bus = EventBus()
    pipeline = SpeechPipeline(tts=FakeTTS(), bus=bus, enable_whisper_wake=False)
    player = HangingPlayer()  # never writes a frame — CU is still working
    pipeline._player = player  # type: ignore[assignment]
    pipeline._speak_playback_ceiling_s = 0.2  # type: ignore[attr-defined]

    play_task = asyncio.create_task(player.play_chunks(_empty_chunks()))

    async def _cu_steps_then_finish() -> None:
        # ~0.6 s of desktop-tool heartbeats — three times the ceiling.
        for _ in range(12):
            pipeline._long_tool_last_activity = time.monotonic()
            await asyncio.sleep(0.05)
        player._release.set()  # CU done → playback completes normally

    heartbeat_task = asyncio.create_task(_cu_steps_then_finish())
    try:
        done = await asyncio.wait_for(
            pipeline._await_playback(play_task, set()), timeout=5.0
        )
    finally:
        heartbeat_task.cancel()

    assert done == {play_task}, "working desktop turn must not be aborted"
    assert player.stop_calls == 0


@pytest.mark.asyncio
async def test_no_first_frame_ceiling_ignores_pre_await_heartbeat() -> None:
    """A heartbeat from BEFORE this playback await began must NOT defer the
    ceiling — per-unit re-arm, the BUG-032 stale-counter lesson. A desktop turn
    that finished moments ago must not grant a later, genuinely-dead playback a
    free pass past the no-first-frame backstop.
    """
    bus = EventBus()
    pipeline = SpeechPipeline(tts=FakeTTS(), bus=bus, enable_whisper_wake=False)
    player = HangingPlayer()
    pipeline._player = player  # type: ignore[assignment]
    pipeline._speak_playback_ceiling_s = 0.2  # type: ignore[attr-defined]
    # Stale: stamped before _await_playback starts its window.
    pipeline._long_tool_last_activity = time.monotonic()

    play_task = asyncio.create_task(player.play_chunks(_empty_chunks()))
    done = await asyncio.wait_for(
        pipeline._await_playback(play_task, set()), timeout=5.0
    )

    assert done == set(), "stale heartbeat must not defer the abort"
    assert player.stop_calls >= 1


@pytest.mark.asyncio
async def test_await_playback_still_aborts_genuine_midplayback_wedge() -> None:
    """A real mid-playback device wedge (frames then freeze) must still abort."""
    bus = EventBus()
    pipeline = SpeechPipeline(tts=FakeTTS(), bus=bus, enable_whisper_wake=False)
    player = WedgeAfterFirstFramePlayer()
    pipeline._player = player  # type: ignore[assignment]
    pipeline._speak_playback_ceiling_s = 10.0  # generous: must NOT be the trigger
    pipeline._speak_playback_stall_s = 0.3  # short stall so the test is fast

    play_task = asyncio.create_task(player.play_chunks(_empty_chunks()))
    try:
        done = await asyncio.wait_for(
            pipeline._await_playback(play_task, set()), timeout=5.0
        )
        assert done == set(), "a frozen mid-playback device must be aborted"
        assert player.aborted is True
    finally:
        if not play_task.done():
            play_task.cancel()
