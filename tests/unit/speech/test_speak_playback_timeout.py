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
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.core.protocols import AudioChunk
from jarvis.speech.pipeline import _BRAIN_TIMEOUT_PHRASE, SpeechPipeline


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


@dataclass
class OwnerTaggedNoFramePlayer:
    """A turn playback that has not produced its own first frame yet.

    Another playback, such as a mission-progress announcement, can update the
    player's global write-progress counter while this task is still waiting.
    The pipeline watchdog must ignore that unrelated progress.
    """

    last_write_ns: int = 0
    last_write_owner_task_id: int | None = None
    stop_calls: int = 0
    _release: asyncio.Event = field(default_factory=asyncio.Event)

    async def play_chunks(self, chunks: AsyncIterator[AudioChunk]) -> None:
        task = asyncio.current_task()
        self.last_write_owner_task_id = id(task) if task is not None else None
        self.last_write_ns = 0
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
async def test_no_first_frame_ceiling_deferred_while_brain_tool_loop_works() -> None:
    """A non-desktop brain tool-use loop (weather / web-search) must not be
    beheaded before the first frame.

    Live bug 2026-06-14 (data/jarvis_desktop.log 14:21 + 14:24, "what's the
    weather in Melbourne"): the router brain ran a multi-round tool-use loop
    (geocoding + DuckDuckGo + open-meteo, ~20 s of genuine work). No
    computer_use step fired, so ``_long_tool_last_activity`` stayed 0 — but the
    brain pinged ``_mark_brain_progress`` on every tool-use-loop round
    (``_brain_last_progress``, the SAME heartbeat the brain stall guard trusts).
    ``_await_playback`` ignored that heartbeat and beheaded the working turn at
    exactly the 20 s ceiling; the user heard "that took too long" and the
    session ended (reason=error). The no-first-frame ceiling must honour the
    brain's round/token heartbeat exactly as it already honours the CU one —
    while the brain is making progress there is legitimately nothing to play.
    """
    bus = EventBus()
    pipeline = SpeechPipeline(tts=FakeTTS(), bus=bus, enable_whisper_wake=False)
    player = HangingPlayer()  # never writes a frame — the brain is still looping
    pipeline._player = player  # type: ignore[assignment]
    pipeline._speak_playback_ceiling_s = 0.2  # type: ignore[attr-defined]

    play_task = asyncio.create_task(player.play_chunks(_empty_chunks()))

    async def _brain_rounds_then_finish() -> None:
        # ~0.6 s of tool-use-loop round heartbeats — three times the ceiling.
        for _ in range(12):
            pipeline._mark_brain_progress()  # → _brain_last_progress = now
            await asyncio.sleep(0.05)
        player._release.set()  # brain produced its answer → playback completes

    heartbeat_task = asyncio.create_task(_brain_rounds_then_finish())
    try:
        done = await asyncio.wait_for(
            pipeline._await_playback(play_task, set()), timeout=5.0
        )
    finally:
        heartbeat_task.cancel()

    assert done == {play_task}, "a working brain tool-loop turn must not be aborted"
    assert player.stop_calls == 0


@pytest.mark.asyncio
async def test_no_first_frame_ceiling_fires_after_brain_heartbeat_stops() -> None:
    """The brain heartbeat must DEFER the ceiling, never SUSPEND it forever.

    Backstop for the 2026-06-14 fix: re-arming the no-first-frame window from
    ``_brain_last_progress`` must not open a "brain pinged once, then wedged" hole.
    Here the brain makes a few rounds of progress and then STOPS dead (a genuine
    provider wedge that produced no text and no audio). Once the heartbeats stop,
    the window must elapse from the LAST ping and the ceiling must still abort —
    same contract as the pre-await-stale-heartbeat test, but mid-stream.
    """
    bus = EventBus()
    pipeline = SpeechPipeline(tts=FakeTTS(), bus=bus, enable_whisper_wake=False)
    player = HangingPlayer()  # never writes a frame
    pipeline._player = player  # type: ignore[assignment]
    pipeline._speak_playback_ceiling_s = 0.2  # type: ignore[attr-defined]

    play_task = asyncio.create_task(player.play_chunks(_empty_chunks()))

    async def _three_pings_then_wedge() -> None:
        for _ in range(3):  # ~0.15 s of life, then silence forever
            pipeline._mark_brain_progress()
            await asyncio.sleep(0.05)
        # No more pings: the brain has wedged with nothing produced.

    heartbeat_task = asyncio.create_task(_three_pings_then_wedge())
    try:
        done = await asyncio.wait_for(
            pipeline._await_playback(play_task, set()), timeout=5.0
        )
    finally:
        heartbeat_task.cancel()

    assert done == set(), "a wedged brain (heartbeat stopped) must still be aborted"
    assert player.stop_calls >= 1


# ---------------------------------------------------------------------------
# Floor guard: a turn that genuinely took under the floor must NEVER speak the
# canned "that took too long" phrase — even when a stale per-turn flag survives
# from a previous slow turn. Live user report 2026-06-14: Jarvis apologised for
# taking too long "right after" a sub-second turn. None of the three timeout
# paths (20 s ceiling / 30 s stall / 30 s total cap) can legitimately fire that
# fast; the only way the phrase reaches TTS is stale boolean state leaking into
# a fresh, fast turn (the no-first-frame mark — an AP-19/BUG-032-class
# process-global flag). The floor guard makes a sub-second turn structurally
# incapable of speaking the phrase, regardless of which stale flag is to blame.
# ---------------------------------------------------------------------------


def _silent_turn_pipeline(
    *, flag: bool, elapsed_s: float, floor: float = 30.0
) -> tuple[SpeechPipeline, list[str]]:
    """A bare pipeline wired only for the empty-/timeout-turn handlers.

    ``elapsed_s`` is how long ago this turn started (the wall-clock the floor
    guard measures); ``flag`` seeds ``_playback_aborted_no_first_frame``. No
    ``_brain`` is set, so the three ``_brain_turn_*`` predicates degrade to
    ``False`` (getattr default) and the flag branch is reached. ``_speak`` /
    ``_set_turn_state`` are stubbed so the test records what was spoken without
    touching audio devices.
    """
    p = SpeechPipeline.__new__(SpeechPipeline)
    now = time.monotonic()
    p._brain_last_progress = now  # type: ignore[attr-defined]
    p._turn_start_monotonic = now - elapsed_s  # type: ignore[attr-defined]
    p._playback_aborted_no_first_frame = flag  # type: ignore[attr-defined]
    p._min_timeout_phrase_s = floor  # type: ignore[attr-defined]
    p._brain_timeout_s = 30.0  # type: ignore[attr-defined]
    p._spoke_this_turn = False  # type: ignore[attr-defined]
    spoken: list[str] = []

    async def _fake_speak(
        text: str, language: str | None = None, *, kind: str = "reply"
    ) -> bool:
        spoken.append(text)
        return False

    async def _fake_state(state: object) -> None:
        return None

    p._speak = _fake_speak  # type: ignore[assignment,method-assign]
    p._set_turn_state = _fake_state  # type: ignore[assignment,method-assign]
    return p, spoken


@pytest.mark.asyncio
async def test_stale_no_first_frame_flag_does_not_speak_timeout_on_fast_turn() -> None:
    """THE headline regression: a sub-second turn with a STALE beheaded flag must
    stay silent — never speak "Das hat zu lange gedauert"."""
    p, spoken = _silent_turn_pipeline(flag=True, elapsed_s=0.1, floor=30.0)

    await p._handle_silent_brain_turn("de", "")

    assert spoken == [], (
        "a sub-second turn must not speak the timeout phrase even with a stale "
        "no-first-frame flag"
    )
    assert p._playback_aborted_no_first_frame is False, "stale flag must be cleared"


@pytest.mark.asyncio
async def test_fast_turn_with_stale_flag_logs_suppressed_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When the floor guard suppresses, it must leave an attributable WARN
    breadcrumb (so the stale-state event is still recorded for root-cause)."""
    p, spoken = _silent_turn_pipeline(flag=True, elapsed_s=0.1, floor=30.0)

    with caplog.at_level(logging.WARNING, logger="jarvis.speech.pipeline"):
        await p._handle_silent_brain_turn("de", "")

    assert spoken == []
    assert any(
        "suppress" in rec.message.lower() and "empty_after_no_first_frame" in rec.message
        for rec in caplog.records
    ), "a suppressed-phrase WARN naming the site must be emitted"


@pytest.mark.asyncio
async def test_spoken_warn_reports_no_first_frame_true_for_attribution(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The consolidated WARN exists to ATTRIBUTE the next real occurrence. For
    the no-first-frame site the ``no_first_frame`` field must report True — the
    flag that drove this path — not False because it was cleared a line too
    early. A genuinely slow (20.83 s) beheaded turn that SPEAKS must log the
    flag as True."""
    p, spoken = _silent_turn_pipeline(flag=True, elapsed_s=20.83, floor=30.0)

    with caplog.at_level(logging.WARNING, logger="jarvis.speech.pipeline"):
        await p._handle_silent_brain_turn("de", "")

    assert spoken == [_BRAIN_TIMEOUT_PHRASE["de"]]
    assert any(
        "spoken" in rec.message
        and "site=empty_after_no_first_frame" in rec.message
        and "no_first_frame=True" in rec.message
        for rec in caplog.records
    ), "the timeout WARN must report no_first_frame=True for this site"


@pytest.mark.asyncio
async def test_genuinely_slow_empty_turn_still_speaks_timeout() -> None:
    """Counter-test: an EMPTY turn that really ran past the floor (a beheaded
    20 s+ turn) must STILL speak the timeout notice — the guard only muzzles
    sub-floor turns, never a real timeout (AD-OE6 zero-silent-drop)."""
    p, spoken = _silent_turn_pipeline(flag=True, elapsed_s=45.0, floor=30.0)

    await p._handle_silent_brain_turn("de", "")

    assert spoken == [_BRAIN_TIMEOUT_PHRASE["de"]], (
        "a genuinely slow beheaded turn must still speak the timeout phrase"
    )


@pytest.mark.asyncio
async def test_floor_guard_skipped_when_turn_anchor_unset() -> None:
    """If the turn-start anchor was never stamped (sentinel 0.0), the guard must
    NOT suppress — we cannot prove the turn was fast, so zero-silent-drop wins."""
    p, spoken = _silent_turn_pipeline(flag=True, elapsed_s=0.1, floor=30.0)
    p._turn_start_monotonic = 0.0  # type: ignore[attr-defined]  # never stamped

    await p._handle_silent_brain_turn("de", "")

    assert spoken == [_BRAIN_TIMEOUT_PHRASE["de"]], (
        "with no turn anchor the guard must default to speaking"
    )


def test_min_timeout_phrase_floor_clamped_to_stall_window() -> None:
    """An operator over-setting the floor ABOVE the stall window must be clamped
    DOWN to the stall window — otherwise a genuine 30 s stall (elapsed 30 s)
    would fall under a 45 s floor and be silently muzzled. The clamp makes
    "suppress a real timeout" structurally impossible."""
    cfg = JarvisConfig()
    cfg.voice.min_timeout_phrase_s = 45.0  # above the 30 s stall window
    pipeline = SpeechPipeline(
        tts=FakeTTS(),
        bus=EventBus(),
        enable_whisper_wake=False,
        config=cfg,
        brain_timeout_s=30.0,
    )
    assert pipeline._min_timeout_phrase_s == 30.0


def test_min_timeout_phrase_floor_below_window_passes_through() -> None:
    """A floor below the stall window is honoured verbatim (no clamp)."""
    cfg = JarvisConfig()
    cfg.voice.min_timeout_phrase_s = 5.0
    pipeline = SpeechPipeline(
        tts=FakeTTS(),
        bus=EventBus(),
        enable_whisper_wake=False,
        config=cfg,
        brain_timeout_s=30.0,
    )
    assert pipeline._min_timeout_phrase_s == 5.0


# ---------------------------------------------------------------------------
# Per-site floor: the no-first-frame path fires at the SHORTER TTS ceiling
# (20 s), not the brain stall window (30 s). Live bug 2026-06-14 16:17 (the
# Berlin→Melbourne research turn): the floor was clamped to the 30 s brain
# stall window, so a real 20.83 s no-first-frame abort was < floor → suppressed
# → the user heard NOTHING and the orb fell to LISTENING. A no-first-frame
# abort can only happen at the 20 s ceiling, so its legitimate floor must track
# that ceiling — never the 30 s brain stall window. The stream-stall /
# total-cap sites keep the 30 s floor (they genuinely fire at the stall window).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_first_frame_abort_at_ceiling_speaks_despite_brain_stall_floor() -> None:
    """THE headline regression (reproduces data/jarvis_desktop.log 16:18:14):
    a real no-first-frame abort at ~20.8 s must SPEAK the timeout notice, even
    though the 30 s brain-stall floor would have suppressed it. RED before the
    per-site floor; GREEN after (no-first-frame floor = 0.5 × 20 s ceiling)."""
    p, spoken = _silent_turn_pipeline(flag=True, elapsed_s=20.83, floor=30.0)

    await p._handle_silent_brain_turn("de", "")

    assert spoken == [_BRAIN_TIMEOUT_PHRASE["de"]], (
        "a genuine 20 s no-first-frame abort must speak, not be swallowed by the "
        "30 s brain-stall floor (the 2026-06-14 silent-research bug)"
    )


@pytest.mark.asyncio
async def test_stream_stall_site_keeps_brain_stall_window_floor() -> None:
    """A stream-stall timeout at ~20 s must STILL be suppressed: that site
    legitimately fires only at the 30 s brain stall window, so a sub-30 s
    elapsed is stale state. This pins that the per-site change does NOT weaken
    the stall/total-cap floors."""
    p, spoken = _silent_turn_pipeline(flag=False, elapsed_s=20.0, floor=30.0)

    await p._speak_brain_timeout("de", site="stream_stall")

    assert spoken == [], (
        "a 20 s stream-stall is below the 30 s brain-stall floor → suppressed"
    )


def test_no_first_frame_phrase_floor_clamped_to_ceiling() -> None:
    """An operator over-setting the no-first-frame floor ABOVE the ceiling must
    be clamped DOWN to the ceiling — otherwise the only time the site can fire
    (the ceiling) would fall under the floor and re-introduce guaranteed
    silence. Mirrors test_min_timeout_phrase_floor_clamped_to_stall_window."""
    cfg = JarvisConfig()
    cfg.voice.no_first_frame_phrase_floor_s = 999.0  # absurd over-set
    pipeline = SpeechPipeline(
        tts=FakeTTS(),
        bus=EventBus(),
        enable_whisper_wake=False,
        config=cfg,
        brain_timeout_s=30.0,
    )
    assert pipeline._no_first_frame_floor_s <= pipeline._speak_playback_ceiling_s


# ---------------------------------------------------------------------------
# Pre-first-token thinking heartbeat: a deep brain can legitimately think for
# tens of seconds before emitting its first token (large context cache + tool
# planning) WITHOUT calling on_progress — so neither _brain_last_progress nor
# _long_tool_last_activity advances and the 20 s no-first-frame ceiling beheads
# a working brain. Live bug 2026-06-14 16:17 (Berlin→Melbourne research):
# Gemini built an 18k-token cache, thought silently ~17 s, ceiling fired
# (since_progress_s=20.19). The stall guard pings a DEDICATED
# _brain_thinking_heartbeat while the brain is pre-first-token; the ceiling
# re-arm honours it. It must stop at first progress (so a wedged TTS after a
# real token is still aborted) and must NOT mask a hung brain (the 90 s hard
# cap, measured from turn start, still fires).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_await_playback_deferred_by_pre_first_token_thinking_heartbeat() -> None:
    """The no-first-frame ceiling must honour _brain_thinking_heartbeat exactly
    as it already honours the CU (_long_tool_last_activity) and brain-round
    (_brain_last_progress) heartbeats. RED before WS2: the re-arm does not read
    the thinking heartbeat, so the still-thinking brain is beheaded."""
    bus = EventBus()
    pipeline = SpeechPipeline(tts=FakeTTS(), bus=bus, enable_whisper_wake=False)
    player = HangingPlayer()  # never writes a frame — the brain is still thinking
    pipeline._player = player  # type: ignore[assignment]
    pipeline._speak_playback_ceiling_s = 0.2  # type: ignore[attr-defined]

    play_task = asyncio.create_task(player.play_chunks(_empty_chunks()))

    async def _thinks_then_finishes() -> None:
        for _ in range(12):  # ~0.6 s of "still thinking" pings — 3× the ceiling
            pipeline._brain_thinking_heartbeat = time.monotonic()  # type: ignore[attr-defined]
            await asyncio.sleep(0.05)
        player._release.set()  # brain produced its answer → playback completes

    hb = asyncio.create_task(_thinks_then_finishes())
    try:
        done = await asyncio.wait_for(
            pipeline._await_playback(play_task, set()), timeout=5.0
        )
    finally:
        hb.cancel()

    assert done == {play_task}, "a still-thinking brain must not be beheaded pre-first-token"
    assert player.stop_calls == 0


@pytest.mark.asyncio
async def test_stall_guard_pings_thinking_heartbeat_pre_first_token() -> None:
    """``_run_brain_with_stall_guard`` must advance ``_brain_thinking_heartbeat``
    while a brain turn is in flight and has made NO progress yet. RED before WS2
    (the guard never touches the signal)."""
    bus = EventBus()
    pipeline = SpeechPipeline(tts=FakeTTS(), bus=bus, enable_whisper_wake=False)
    pipeline._brain_stall_poll_s = 0.02  # type: ignore[attr-defined]
    pipeline._brain_timeout_s = 10.0  # type: ignore[attr-defined]
    pipeline._brain_hard_timeout_s = 10.0  # type: ignore[attr-defined]

    samples: list[float] = []

    async def _silent_think() -> tuple[str, bool]:
        for _ in range(10):  # ~0.2 s of pure thinking — no chunk, no progress ping
            samples.append(getattr(pipeline, "_brain_thinking_heartbeat", 0.0))
            await asyncio.sleep(0.02)
        return ("done", False)

    result = await asyncio.wait_for(
        pipeline._run_brain_with_stall_guard(_silent_think()), timeout=5.0
    )

    assert result == ("done", False)
    advanced = [s for s in samples if s > 0.0]
    assert len(advanced) >= 3, f"thinking heartbeat barely advanced: {samples}"
    assert advanced == sorted(advanced), "heartbeat must be monotonic"


@pytest.mark.asyncio
async def test_thinking_heartbeat_stops_after_first_progress() -> None:
    """Once the brain makes real progress (first token → _mark_brain_progress),
    the dedicated thinking heartbeat must FREEZE: the real _brain_last_progress
    signal governs from then on, so a brain that produces a token and then wedges
    its TTS is still aborted by the no-first-frame ceiling (the thinking
    heartbeat must not paper over a post-token wedge). RED before WS2."""
    bus = EventBus()
    pipeline = SpeechPipeline(tts=FakeTTS(), bus=bus, enable_whisper_wake=False)
    pipeline._brain_stall_poll_s = 0.02  # type: ignore[attr-defined]
    pipeline._brain_timeout_s = 10.0  # type: ignore[attr-defined]
    pipeline._brain_hard_timeout_s = 10.0  # type: ignore[attr-defined]

    async def _think_then_progress() -> tuple[str, bool]:
        await asyncio.sleep(0.1)  # pre-token thinking — heartbeat advances
        assert getattr(pipeline, "_brain_thinking_heartbeat", 0.0) > 0.0
        pipeline._mark_brain_progress()  # first token arrives
        await asyncio.sleep(0.08)  # let the poll loop observe the progress
        frozen = pipeline._brain_thinking_heartbeat  # type: ignore[attr-defined]
        await asyncio.sleep(0.12)  # post-token silence
        assert pipeline._brain_thinking_heartbeat == frozen, (  # type: ignore[attr-defined]
            "thinking heartbeat must freeze once real progress is seen"
        )
        return ("done", False)

    result = await asyncio.wait_for(
        pipeline._run_brain_with_stall_guard(_think_then_progress()), timeout=5.0
    )
    assert result == ("done", False)


@pytest.mark.asyncio
async def test_hung_brain_times_out_despite_thinking_heartbeat() -> None:
    """Safety: the thinking heartbeat defers the TTS ceiling but must NOT mask a
    genuinely hung brain — the absolute hard cap (measured from turn start, never
    reset by the heartbeat) must still raise TimeoutError."""
    bus = EventBus()
    pipeline = SpeechPipeline(tts=FakeTTS(), bus=bus, enable_whisper_wake=False)
    pipeline._brain_stall_poll_s = 0.02  # type: ignore[attr-defined]
    pipeline._brain_timeout_s = 10.0  # type: ignore[attr-defined]
    pipeline._brain_hard_timeout_s = 0.3  # type: ignore[attr-defined]  # must fire

    async def _never_returns() -> tuple[str, bool]:
        await asyncio.Event().wait()
        return ("", False)  # pragma: no cover - never reached

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(
            pipeline._run_brain_with_stall_guard(_never_returns()), timeout=5.0
        )


@pytest.mark.asyncio
async def test_await_playback_ignores_unrelated_announcement_audio_progress() -> None:
    """A background announcement must not satisfy the current turn's playback.

    Live regression 2026-06-25: a spawn heartbeat spoke while a simple question
    was still in the brain/tool path. The heartbeat updated ``last_write_ns`` on
    the shared player; the current answer had not produced its own first frame
    yet, but ``_await_playback`` treated the stale foreign timestamp as
    mid-playback progress and fired the 5 s device-wedge abort. The generated
    answer was recorded in the transcript but not reliably spoken.
    """
    bus = EventBus()
    pipeline = SpeechPipeline(tts=FakeTTS(), bus=bus, enable_whisper_wake=False)
    player = OwnerTaggedNoFramePlayer()
    pipeline._player = player  # type: ignore[assignment]
    pipeline._speak_playback_ceiling_s = 0.2  # type: ignore[attr-defined]
    pipeline._speak_playback_stall_s = 0.08  # type: ignore[attr-defined]

    play_task = asyncio.create_task(player.play_chunks(_empty_chunks()))

    async def _foreign_audio_then_brain_finishes() -> None:
        await asyncio.sleep(0.03)
        # Simulate AudioPlayer progress from a different play_chunks task.
        player.last_write_owner_task_id = -1
        player.last_write_ns = time.monotonic_ns()
        for _ in range(8):
            pipeline._brain_thinking_heartbeat = time.monotonic()  # type: ignore[attr-defined]
            await asyncio.sleep(0.04)
        player._release.set()

    foreign = asyncio.create_task(_foreign_audio_then_brain_finishes())
    try:
        done = await asyncio.wait_for(
            pipeline._await_playback(play_task, set()), timeout=5.0
        )
    finally:
        foreign.cancel()

    assert done == {play_task}, "foreign announcement audio must not abort this turn"
    assert player.stop_calls == 0


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
