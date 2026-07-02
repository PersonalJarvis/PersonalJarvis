"""Async announcements must not barge a user who is holding the floor.

Structural backstop for AD-OE5 ("speak ONLY at the next turn-boundary, never
interrupt mid-utterance"). Every asynchronous TTS surface funnels through
``_on_announcement``: the Pre-Thinking-Ack, mission-completion readbacks, the
Computer-Use failure readback, workflow completions. None of them checked the
turn-state before playing, so any of them could speak straight over a user who
was still talking (``_turn_state == USER_SPEAKING``).

Contract pinned here:
  * A ``priority != "interrupt"`` PREAMBLE that arrives while the user holds the
    floor is dropped — it is ephemeral ("I'm about to think about this") and
    stale by the time the user finishes.
  * A COMPLETION/readback that arrives while the user holds the floor is
    deferred and flushed at the next turn-boundary (AD-OE6 zero-silent-drop) —
    the user still needs that answer.
  * A ``priority == "interrupt"`` announcement still punches through (the
    deliberate barge — e.g. a MissionFailed terminal readback).
  * Nothing changes when the user is NOT speaking (idle/processing).
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.events import AnnouncementRequested
from jarvis.core.protocols import AudioChunk
from jarvis.speech.pipeline import SpeechPipeline, TurnTakingState


@dataclass
class FakeTTS:
    calls: list[tuple[str, str | None]] = field(default_factory=list)
    name: str = "fake-tts"
    supports_streaming: bool = True

    async def synthesize(
        self,
        text: str,
        voice: str | None = None,
        language_code: str | None = None,
    ) -> AsyncIterator[AudioChunk]:
        self.calls.append((text, language_code))
        if False:  # pragma: no cover
            yield  # type: ignore[unreachable]


@dataclass
class FakePlayer:
    stop_calls: int = 0
    plays: int = 0
    last_should_play: object = None

    async def play_chunks(
        self,
        chunks: AsyncIterator[AudioChunk],
        *,
        should_play=None,
    ) -> None:
        self.last_should_play = should_play
        # Mirror the real player: a False staleness verdict drops the playback.
        if should_play is not None and not should_play():
            async for _ in chunks:
                pass
            return
        self.plays += 1
        async for _ in chunks:
            pass

    def stop(self) -> None:
        self.stop_calls += 1


def _make_pipeline(tts: FakeTTS, bus: EventBus, player: FakePlayer) -> SpeechPipeline:
    pipeline = SpeechPipeline(tts=tts, bus=bus, enable_whisper_wake=False)
    pipeline._player = player  # type: ignore[assignment]
    return pipeline


async def _flush_deferred() -> None:
    tasks = [
        t
        for t in asyncio.all_tasks()
        if t.get_name() == "deferred-announcement" and not t.done()
    ]
    if tasks:
        await asyncio.gather(*tasks)


@pytest.mark.asyncio
async def test_preamble_while_user_speaking_is_dropped() -> None:
    bus = EventBus()
    tts = FakeTTS()
    player = FakePlayer()
    pipeline = _make_pipeline(tts, bus, player)
    pipeline._turn_state = TurnTakingState.USER_SPEAKING  # type: ignore[attr-defined]

    await bus.publish(
        AnnouncementRequested(
            text="Ich rufe die Konfigurationsparameter auf.",
            language="de",
            priority="normal",
            kind="preamble",
        )
    )

    assert tts.calls == [], "a preamble spoke over a user who was still talking"
    assert player.plays == 0


@pytest.mark.asyncio
async def test_completion_while_user_speaking_is_deferred_then_flushed() -> None:
    bus = EventBus()
    tts = FakeTTS()
    player = FakePlayer()
    pipeline = _make_pipeline(tts, bus, player)
    pipeline._turn_state = TurnTakingState.USER_SPEAKING  # type: ignore[attr-defined]

    await bus.publish(
        AnnouncementRequested(
            text="Fertig. Das Ergebnis liegt bereit.",
            language="de",
            priority="normal",
            kind="completion",
        )
    )

    # Held back while the user holds the floor — not spoken yet.
    assert player.plays == 0, "a completion barged the user instead of deferring"

    # Turn-boundary: the user stopped → the deferred readback is flushed.
    await pipeline._set_turn_state(TurnTakingState.LISTENING)
    await _flush_deferred()

    assert tts.calls == [("Fertig. Das Ergebnis liegt bereit.", "de-DE")]
    assert player.plays == 1


@pytest.mark.asyncio
async def test_interrupt_priority_punches_through_user_speaking() -> None:
    bus = EventBus()
    tts = FakeTTS()
    player = FakePlayer()
    pipeline = _make_pipeline(tts, bus, player)
    pipeline._turn_state = TurnTakingState.USER_SPEAKING  # type: ignore[attr-defined]

    await bus.publish(
        AnnouncementRequested(
            text="Die Mission ist fehlgeschlagen.",
            language="de",
            priority="interrupt",
        )
    )

    assert tts.calls == [("Die Mission ist fehlgeschlagen.", "de-DE")]
    assert player.plays == 1


@pytest.mark.asyncio
async def test_background_completion_while_user_speaking_is_deferred() -> None:
    """The direct OpenClaw-background readback path (which bypasses
    ``_on_announcement`` and plays straight to the player) must also respect the
    floor: defer while the user speaks, flush at the boundary."""
    from jarvis.core.events import JarvisAgentBackgroundCompleted

    bus = EventBus()
    tts = FakeTTS()
    player = FakePlayer()
    pipeline = _make_pipeline(tts, bus, player)
    pipeline._turn_state = TurnTakingState.USER_SPEAKING  # type: ignore[attr-defined]

    await bus.publish(
        JarvisAgentBackgroundCompleted(
            success=True, summary="Der Bericht liegt bereit.", duration_s=42.0
        )
    )

    assert player.plays == 0, "a background completion barged the user mid-utterance"

    await pipeline._set_turn_state(TurnTakingState.LISTENING)
    await _flush_deferred()

    assert player.plays == 1
    assert tts.calls and "Fertig" in tts.calls[0][0]


@pytest.mark.asyncio
async def test_preamble_while_jarvis_speaking_is_dropped() -> None:
    """Regression (2026-06-20 'Ich schaue mir jetzt …' spoken AFTER the answer):
    a preamble that reaches the handler once the answer is already being voiced
    (``JARVIS_SPEAKING``) is stale and must be dropped — never queued behind the
    answer. The pre-existing floor guard only covered USER_SPEAKING/LISTENING;
    JARVIS_SPEAKING (the answer's own playback) was the missing case."""
    bus = EventBus()
    tts = FakeTTS()
    player = FakePlayer()
    pipeline = _make_pipeline(tts, bus, player)
    pipeline._turn_state = TurnTakingState.JARVIS_SPEAKING  # type: ignore[attr-defined]

    await bus.publish(
        AnnouncementRequested(
            text="Ich schaue mir jetzt die GitHub-CLI-Repositories an.",
            language="de",
            priority="normal",
            kind="preamble",
        )
    )

    assert tts.calls == [], "a stale preamble spoke after the answer started"
    assert player.plays == 0


@pytest.mark.asyncio
async def test_preamble_during_processing_carries_staleness_gate() -> None:
    """While the brain is still thinking (``PROCESSING``) the preamble plays —
    AND it is handed a ``should_play`` predicate so the player drops it if the
    answer overtakes it during synthesis / the play-lock wait (the race the
    lazy play-lock fix narrows but does not eliminate)."""
    bus = EventBus()
    tts = FakeTTS()
    player = FakePlayer()
    pipeline = _make_pipeline(tts, bus, player)
    pipeline._turn_state = TurnTakingState.PROCESSING  # type: ignore[attr-defined]

    await bus.publish(
        AnnouncementRequested(
            text="Ich prüfe gerade den Google-Cloud-Status über die CLI.",
            language="de",
            priority="normal",
            kind="preamble",
        )
    )

    assert player.plays == 1, "the preamble should play during the thinking gap"
    pred = player.last_should_play
    assert callable(pred), "a preamble must carry a staleness predicate"
    # Still thinking → valid.
    assert pred() is True
    # The answer starts voicing mid-flight → the predicate now reports stale,
    # so the player would drop the still-unplayed preamble.
    pipeline._turn_state = TurnTakingState.JARVIS_SPEAKING  # type: ignore[attr-defined]
    assert pred() is False


async def _await_turn_state_tasks() -> None:
    tasks = [
        t
        for t in asyncio.all_tasks()
        if t.get_name().startswith("turn-state-") and not t.done()
    ]
    if tasks:
        await asyncio.gather(*tasks)


@pytest.mark.asyncio
async def test_discarded_false_start_releases_the_floor() -> None:
    """Live 2026-07-02 19:06: a 96 ms VAD blip set USER_SPEAKING, the false
    start was discarded WITHOUT a state transition, and the mission's
    completion readback sat deferred for 31 s ("user holds the floor") until
    the hangup forced a transition. A discarded false start must release the
    floor so deferred announcements flush immediately."""
    bus = EventBus()
    tts = FakeTTS()
    player = FakePlayer()
    pipeline = _make_pipeline(tts, bus, player)
    pipeline._turn_state = TurnTakingState.USER_SPEAKING  # type: ignore[attr-defined]

    await bus.publish(
        AnnouncementRequested(
            text="Erledigt. Das Fenster ist offen.",
            language="de",
            priority="normal",
            kind="completion",
        )
    )
    assert player.plays == 0  # correctly deferred while the floor was held

    pipeline._on_vad_endpoint("false_start")
    await _await_turn_state_tasks()
    await _flush_deferred()

    assert pipeline._turn_state == TurnTakingState.LISTENING
    assert player.plays == 1, (
        "the completion stayed deferred behind a discarded VAD false start"
    )


@pytest.mark.asyncio
async def test_false_start_release_never_regresses_a_newer_state() -> None:
    """The release is guarded: if the state already moved on (e.g. the answer
    started voicing), a late false-start endpoint must not yank it back."""
    bus = EventBus()
    pipeline = _make_pipeline(FakeTTS(), bus, FakePlayer())
    pipeline._turn_state = TurnTakingState.JARVIS_SPEAKING  # type: ignore[attr-defined]

    pipeline._on_vad_endpoint("false_start")
    await _await_turn_state_tasks()

    assert pipeline._turn_state == TurnTakingState.JARVIS_SPEAKING


@pytest.mark.asyncio
async def test_announcement_while_idle_is_unaffected() -> None:
    """Regression guard: the guard only arms while the user holds the floor."""
    bus = EventBus()
    tts = FakeTTS()
    player = FakePlayer()
    # The bus keeps the pipeline alive via its _on_announcement subscription.
    _make_pipeline(tts, bus, player)
    # Default turn-state is IDLE — the user is not speaking.

    await bus.publish(
        AnnouncementRequested(
            text="Ich schaue kurz nach.",
            language="de",
            priority="normal",
            kind="preamble",
        )
    )

    assert tts.calls == [("Ich schaue kurz nach.", "de-DE")]
    assert player.plays == 1
