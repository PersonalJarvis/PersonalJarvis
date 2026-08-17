"""Instant acknowledgment on the classic speech pipeline (2026-08-17).

The contract (jarvis/voice/instant_ack.py): a heavy turn gets its first sign
of life at dispatch time from the deterministic turn plan -- research, screen
and mission work immediately, actions and personal lookups after a short
grace and only if the turn is still processing. Actions get a request-specific
line from the flash composer or nothing. The router's grounded ack that
follows within seconds is dropped (no double-tap); the instant ack itself is
exempt from the anti-loop cap because it is one line per user utterance.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.events import AnnouncementRequested
from jarvis.core.protocols import AudioChunk
from jarvis.speech.pipeline import SpeechPipeline, TurnTakingState
from jarvis.voice import instant_ack as instant_ack_module


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
    plays: int = 0

    async def play_chunks(
        self,
        chunks: AsyncIterator[AudioChunk],
        *,
        should_play=None,
    ) -> None:
        if should_play is not None and not should_play():
            async for _ in chunks:
                pass
            return
        self.plays += 1
        async for _ in chunks:
            pass

    def stop(self) -> None:
        return None


class _FakeComposer:
    def __init__(self, line: str = "") -> None:
        self.line = line
        self.calls: list[dict] = []

    @property
    def has_llm(self) -> bool:
        return True

    async def compose(self, **kwargs):
        self.calls.append(kwargs)
        return self.line or kwargs["canned"]()


def _make_pipeline(
    tts: FakeTTS,
    bus: EventBus,
    player: FakePlayer,
    *,
    instant_ack: bool = True,
    composer: _FakeComposer | None = None,
) -> SpeechPipeline:
    pipeline = SpeechPipeline(tts=tts, bus=bus, enable_whisper_wake=False)
    pipeline._player = player  # type: ignore[assignment]
    pipeline._config = SimpleNamespace(  # type: ignore[attr-defined]
        ack_brain=SimpleNamespace(
            suppress_preamble_after_interrupt_ms=5000,
            grounded_ack_commit_grace_ms=0,
            preamble_dedup_window_s=180,
            preamble_rate_limit_per_min=3,
            instant_ack=instant_ack,
        )
    )
    pipeline._brain = SimpleNamespace(_readback_composer=composer)  # type: ignore[attr-defined]
    pipeline._turn_state = TurnTakingState.PROCESSING
    pipeline._brain_first_frame_played = False
    return pipeline


def _instant_acks(published: list[AnnouncementRequested]) -> list[AnnouncementRequested]:
    return [e for e in published if e.source_layer == "brain.instant_ack"]


async def _settle(seconds: float = 0.15) -> None:
    await asyncio.sleep(seconds)


@pytest.mark.asyncio
async def test_research_turn_speaks_immediately(monkeypatch) -> None:
    """LONG work: the pooled line goes out at once and reaches the speaker."""
    monkeypatch.setattr(
        instant_ack_module,
        "_POOLS",
        {
            **instant_ack_module._POOLS,
            instant_ack_module.WorkClass.RESEARCH: {
                "de": ("Ich suche das gerade online.",),  # i18n-allow: fixture
                "en": ("I'm looking that up online.",),
                "es": ("Lo estoy buscando en línea.",),
            },
        },
    )
    bus = EventBus()
    seen: list[AnnouncementRequested] = []
    bus.subscribe(AnnouncementRequested, lambda e: seen.append(e))
    tts = FakeTTS()
    player = FakePlayer()
    pipeline = _make_pipeline(tts, bus, player)

    pipeline._arm_instant_ack("What's the weather in Berlin right now?", "en")
    await _settle()

    acks = _instant_acks(seen)
    assert [a.text for a in acks] == ["I'm looking that up online."]
    assert acks[0].kind == "preamble"
    assert player.plays == 1
    assert tts.calls[0][0] == "I'm looking that up online."
    # The moment of speaking is recorded for the continue-not-repeat rule.
    assert pipeline._instant_ack_spoke_recently(5.0)


@pytest.mark.asyncio
async def test_plain_conversation_and_voice_control_arm_nothing() -> None:
    bus = EventBus()
    seen: list[AnnouncementRequested] = []
    bus.subscribe(AnnouncementRequested, lambda e: seen.append(e))
    tts = FakeTTS()
    player = FakePlayer()
    pipeline = _make_pipeline(tts, bus, player)

    pipeline._arm_instant_ack("Hallo, wie geht's?", "de")  # i18n-allow: fixture
    pipeline._arm_instant_ack("Sei still", "de")  # i18n-allow: fixture
    pipeline._arm_instant_ack("When was Ada Lovelace born?", "en")
    await _settle()

    assert _instant_acks(seen) == []
    assert player.plays == 0
    assert getattr(pipeline, "_instant_ack_task", None) is None


@pytest.mark.asyncio
async def test_kill_switch_arms_nothing() -> None:
    bus = EventBus()
    seen: list[AnnouncementRequested] = []
    bus.subscribe(AnnouncementRequested, lambda e: seen.append(e))
    pipeline = _make_pipeline(FakeTTS(), bus, FakePlayer(), instant_ack=False)

    pipeline._arm_instant_ack("What's the weather in Berlin right now?", "en")
    await _settle()

    assert _instant_acks(seen) == []


@pytest.mark.asyncio
async def test_short_work_waits_the_grace_and_speaks_only_if_still_processing(
    monkeypatch,
) -> None:
    """A personal lookup answered inside the grace stays chatter-free; one that
    outlasts it gets the line."""
    monkeypatch.setattr(instant_ack_module, "SHORT_GRACE_S", 0.1)
    bus = EventBus()
    seen: list[AnnouncementRequested] = []
    bus.subscribe(AnnouncementRequested, lambda e: seen.append(e))
    tts = FakeTTS()
    player = FakePlayer()
    pipeline = _make_pipeline(tts, bus, player)

    # Fast answer: the turn leaves PROCESSING before the grace elapses.
    pipeline._arm_instant_ack("What's in my notes about Albel?", "en")
    await asyncio.sleep(0.02)
    pipeline._turn_state = TurnTakingState.JARVIS_SPEAKING
    await _settle(0.25)
    assert _instant_acks(seen) == []

    # Slow answer: still PROCESSING after the grace -> the line is spoken.
    pipeline._turn_state = TurnTakingState.PROCESSING
    pipeline._arm_instant_ack("What's in my notes about Albel?", "en")
    await _settle(0.35)
    acks = _instant_acks(seen)
    assert len(acks) == 1
    assert acks[0].text in instant_ack_module.instant_ack_pool(
        instant_ack_module.WorkClass.PERSONAL, "en"
    )
    assert player.plays == 1


@pytest.mark.asyncio
async def test_action_without_composer_stays_silent(monkeypatch) -> None:
    """No stock 'on it' for an action: without a composer nothing is spoken."""
    monkeypatch.setattr(instant_ack_module, "SHORT_GRACE_S", 0.05)
    bus = EventBus()
    seen: list[AnnouncementRequested] = []
    bus.subscribe(AnnouncementRequested, lambda e: seen.append(e))
    pipeline = _make_pipeline(FakeTTS(), bus, FakePlayer(), composer=None)

    pipeline._arm_instant_ack("Open Spotify", "en")
    await _settle(0.3)

    assert _instant_acks(seen) == []


@pytest.mark.asyncio
async def test_action_with_composer_speaks_a_request_specific_line(monkeypatch) -> None:
    monkeypatch.setattr(instant_ack_module, "SHORT_GRACE_S", 0.05)
    bus = EventBus()
    seen: list[AnnouncementRequested] = []
    bus.subscribe(AnnouncementRequested, lambda e: seen.append(e))
    composer = _FakeComposer("I'm opening Spotify.")
    tts = FakeTTS()
    player = FakePlayer()
    pipeline = _make_pipeline(tts, bus, player, composer=composer)

    pipeline._arm_instant_ack("Open Spotify", "en")
    await _settle(0.3)

    assert [a.text for a in _instant_acks(seen)] == ["I'm opening Spotify."]
    assert composer.calls[0]["in_progress"] is True
    assert "Open Spotify" in composer.calls[0]["instruction"]
    assert player.plays == 1


@pytest.mark.asyncio
async def test_action_composer_line_that_claims_a_result_is_rejected(monkeypatch) -> None:
    monkeypatch.setattr(instant_ack_module, "SHORT_GRACE_S", 0.05)
    bus = EventBus()
    seen: list[AnnouncementRequested] = []
    bus.subscribe(AnnouncementRequested, lambda e: seen.append(e))
    composer = _FakeComposer("Spotify is open now.")
    pipeline = _make_pipeline(FakeTTS(), bus, FakePlayer(), composer=composer)

    pipeline._arm_instant_ack("Open Spotify", "en")
    await _settle(0.3)

    assert _instant_acks(seen) == []


@pytest.mark.asyncio
async def test_router_ack_shortly_after_an_instant_ack_is_dropped(monkeypatch) -> None:
    """The grounded router ack seconds after the instant ack is the double-tap;
    once the wait has grown past the progress threshold it may speak again."""
    monkeypatch.setattr(
        instant_ack_module,
        "_POOLS",
        {
            **instant_ack_module._POOLS,
            instant_ack_module.WorkClass.RESEARCH: {
                "de": ("Ich suche das gerade online.",),  # i18n-allow: fixture
                "en": ("I'm looking that up online.",),
                "es": ("Lo estoy buscando en línea.",),
            },
        },
    )
    bus = EventBus()
    tts = FakeTTS()
    player = FakePlayer()
    pipeline = _make_pipeline(tts, bus, player)

    pipeline._arm_instant_ack("What's the weather in Berlin right now?", "en")
    await _settle()
    assert player.plays == 1

    await bus.publish(
        AnnouncementRequested(
            text="Checking your calendar for tomorrow.",
            language="en",
            priority="normal",
            kind="preamble",
            source_layer="brain.router.ack",
        )
    )
    assert player.plays == 1, "the router ack double-tapped the instant ack"

    # Long wait: the router ack becomes the progress line.
    pipeline._instant_ack_spoken_at -= 30.0
    last_text, last_at = pipeline._last_preamble_spoken
    pipeline._last_preamble_spoken = (last_text, last_at - 30.0)
    await bus.publish(
        AnnouncementRequested(
            text="Still checking your calendar for tomorrow.",
            language="en",
            priority="normal",
            kind="preamble",
            source_layer="brain.router.ack",
        )
    )
    assert player.plays == 2


@pytest.mark.asyncio
async def test_instant_ack_is_exempt_from_the_anti_loop_cap() -> None:
    """Four heavy commands in a minute -> four first-signs-of-life. The cap
    still applies to every other emitter."""
    bus = EventBus()
    tts = FakeTTS()
    player = FakePlayer()
    _make_pipeline(tts, bus, player)

    for i in range(5):
        await bus.publish(
            AnnouncementRequested(
                text=f"Instant line number {i}.",
                language="en",
                priority="normal",
                kind="preamble",
                source_layer="brain.instant_ack",
            )
        )
    assert player.plays == 5

    for i in range(5):
        await bus.publish(
            AnnouncementRequested(
                text=f"Other line number {i}.",
                language="en",
                priority="normal",
                kind="preamble",
                source_layer="brain.other",
            )
        )
    assert player.plays == 8, "the anti-loop cap must still bind other emitters"


@pytest.mark.asyncio
async def test_a_new_utterance_cancels_the_pending_ack_of_the_previous_one(
    monkeypatch,
) -> None:
    monkeypatch.setattr(instant_ack_module, "SHORT_GRACE_S", 0.2)
    bus = EventBus()
    seen: list[AnnouncementRequested] = []
    bus.subscribe(AnnouncementRequested, lambda e: seen.append(e))
    pipeline = _make_pipeline(FakeTTS(), bus, FakePlayer())

    pipeline._arm_instant_ack("What's in my notes about Albel?", "en")
    first = pipeline._instant_ack_task
    pipeline._arm_instant_ack("Hallo", "de")  # i18n-allow: fixture
    await _settle(0.35)

    assert first is not None and first.cancelled()
    assert _instant_acks(seen) == []


@pytest.mark.asyncio
async def test_long_turn_gets_one_progress_line_grounded_in_the_running_tool(
    monkeypatch,
) -> None:
    """Instant ack, then — after PROGRESS_AFTER_S with the turn still
    processing — ONE line that names the tool actually running (search_web
    -> the search pool). A handover (spawn_worker) speaks nothing: its reply
    states the handover."""
    from jarvis.core.events import ActionProposed
    from jarvis.speech import pipeline as pipeline_module

    monkeypatch.setattr(pipeline_module, "PROGRESS_AFTER_S", 0.2)
    monkeypatch.setattr(
        instant_ack_module,
        "_POOLS",
        {
            **instant_ack_module._POOLS,
            instant_ack_module.WorkClass.RESEARCH: {
                "de": ("Ich suche das gerade online.",),  # i18n-allow: fixture
                "en": ("I'm looking that up online.",),
                "es": ("Lo estoy buscando en línea.",),
            },
        },
    )
    bus = EventBus()
    seen: list[AnnouncementRequested] = []
    bus.subscribe(AnnouncementRequested, lambda e: seen.append(e))
    tts = FakeTTS()
    player = FakePlayer()
    pipeline = _make_pipeline(tts, bus, player)

    pipeline._arm_instant_ack("What's the weather in Berlin right now?", "en")
    await _settle(0.05)
    await bus.publish(ActionProposed(tool_name="search_web"))
    await _settle(0.35)

    texts = [a.text for a in _instant_acks(seen)]
    assert texts[0] == "I'm looking that up online."
    assert len(texts) == 2
    assert texts[1] in instant_ack_module.progress_pool(
        instant_ack_module.ToolActivity.SEARCH, "en"
    )
    assert player.plays == 2
    # Nothing more while the turn keeps running: one progress line per turn.
    await _settle(0.3)
    assert len(_instant_acks(seen)) == 2

    # Handover: the spawn reply states it — no progress line.
    seen.clear()
    pipeline._arm_instant_ack("What's the weather in Berlin right now?", "en")
    await _settle(0.05)
    await bus.publish(ActionProposed(tool_name="spawn_worker"))
    await _settle(0.4)
    assert len(_instant_acks(seen)) == 1


@pytest.mark.asyncio
async def test_progress_line_yields_to_a_router_ack_that_just_spoke(monkeypatch) -> None:
    """If the grounded router ack covered the wait seconds ago, the progress
    line stays silent (no two interim lines within the window)."""
    from jarvis.speech import pipeline as pipeline_module

    monkeypatch.setattr(pipeline_module, "PROGRESS_AFTER_S", 0.2)
    bus = EventBus()
    seen: list[AnnouncementRequested] = []
    bus.subscribe(AnnouncementRequested, lambda e: seen.append(e))
    tts = FakeTTS()
    player = FakePlayer()
    pipeline = _make_pipeline(tts, bus, player)

    pipeline._arm_instant_ack("What's the weather in Berlin right now?", "en")
    await _settle(0.05)
    assert player.plays == 1
    # A router ack 0.25 s later is still inside the window -> dropped; then
    # simulate that one DID speak just before the progress deadline.
    pipeline._last_preamble_spoken = ("Checking the weather for you.", time.monotonic())
    await _settle(0.35)
    assert len(_instant_acks(seen)) == 1, "the progress line spoke over a fresh interim line"
