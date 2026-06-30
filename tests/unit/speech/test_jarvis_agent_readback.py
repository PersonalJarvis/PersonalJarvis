"""Sub-agent / mission report readback behaviour (2026-06-19).

Three behaviours, all on the out-of-band announcement path:

* **Distinct attribution** — a spawned sub-agent / mission / worker readback is
  tagged ``spoken_kind="subagent"`` (the attributed sibling of ``completion``),
  yet keeps the exact same hangup punch-through + afterglow handling so an
  offloaded result is never silently dropped (AD-OE6).
* **Speaking indicator** — the readback drives the Supervisor ``SPEAKING`` state
  while it plays (so the mascot/orb animates), then restores the prior state.
* **Keep listening** — the OpenClaw-background direct path stamps the readback
  grace timestamp so ``_active_session`` keeps the mic open afterward.

Companion to ``test_announcement_bridge.py``; reuses its fake shapes.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.events import (
    AnnouncementRequested,
    JarvisAgentBackgroundCompleted,
    SpeechSpoken,
    SystemStateChanged,
)
from jarvis.core.protocols import AudioChunk
from jarvis.sessions.constants import SPOKEN_KIND_SUBAGENT
from jarvis.speech.pipeline import SpeechPipeline, _announcement_spoken_kind
from jarvis.state.supervisor import Supervisor


@dataclass
class FakeTTS:
    name: str = "fake-tts"
    supports_streaming: bool = True
    calls: list[tuple[str, str | None]] = field(default_factory=list)

    async def synthesize(
        self, text: str, voice: str | None = None, language_code: str | None = None
    ) -> AsyncIterator[AudioChunk]:
        self.calls.append((text, language_code))
        if False:  # pragma: no cover
            yield  # type: ignore[unreachable]


@dataclass
class FakePlayer:
    stop_calls: int = 0
    plays: int = 0

    async def play_chunks(self, chunks: AsyncIterator[AudioChunk]) -> None:
        self.plays += 1
        async for _ in chunks:
            pass

    def stop(self) -> None:
        self.stop_calls += 1


def _make_pipeline(
    tts: FakeTTS, bus: EventBus, player: FakePlayer | None = None
) -> SpeechPipeline:
    pipeline = SpeechPipeline(tts=tts, bus=bus, enable_whisper_wake=False)
    if player is not None:
        pipeline._player = player  # type: ignore[assignment]
    return pipeline


# --------------------------------------------------------------------------
# Task 2 — distinct `subagent` kind, every readback guard intact
# --------------------------------------------------------------------------


def test_announcement_spoken_kind_maps_subagent_to_itself() -> None:
    assert _announcement_spoken_kind("subagent") == SPOKEN_KIND_SUBAGENT


@pytest.mark.asyncio
async def test_subagent_announcement_punches_through_hangup() -> None:
    """A kind="subagent" readback is the offloaded answer — it must be spoken
    even after the user hung up, exactly like kind="completion" (AD-OE5/OE6)."""
    bus = EventBus()
    tts = FakeTTS()
    player = FakePlayer()
    pipeline = _make_pipeline(tts, bus, player)
    pipeline._hangup_event.set()  # type: ignore[attr-defined]

    await bus.publish(
        AnnouncementRequested(
            text="Deine Recherche ist fertig.",
            language="de",
            priority="normal",
            kind="subagent",
        )
    )

    assert tts.calls == [("Deine Recherche ist fertig.", "de-DE")]
    assert player.plays == 1


@pytest.mark.asyncio
async def test_subagent_announcement_recorded_with_subagent_kind() -> None:
    """The voiced readback is logged as a SpeechSpoken with the subagent tag, so
    the transcript renders it on the 'Jarvis Sub-Agent / Output' track."""
    bus = EventBus()
    tts = FakeTTS()
    player = FakePlayer()
    spoken: list[SpeechSpoken] = []
    bus.subscribe(SpeechSpoken, lambda ev: spoken.append(ev))
    _make_pipeline(tts, bus, player)

    await bus.publish(
        AnnouncementRequested(text="Erledigt.", language="de", kind="subagent")
    )

    assert spoken, "subagent readback was not emitted as a SpeechSpoken event"
    assert spoken[0].spoken_kind == SPOKEN_KIND_SUBAGENT


# --------------------------------------------------------------------------
# Task 3 — speaking indicator during the readback
# --------------------------------------------------------------------------


@dataclass
class StateProbePlayer:
    """Captures the Supervisor state that is live WHILE the readback plays."""

    supervisor: Supervisor
    state_during_play: str | None = None
    plays: int = 0

    async def play_chunks(self, chunks: AsyncIterator[AudioChunk]) -> None:
        self.plays += 1
        self.state_during_play = self.supervisor.state
        async for _ in chunks:
            pass

    def stop(self) -> None:  # pragma: no cover - not used here
        pass


@pytest.mark.asyncio
async def test_readback_animates_speaking_then_restores_to_listening() -> None:
    """While the readback plays the UI state is SPEAKING (mascot/orb animates);
    when the user is still present (no hangup) it restores to LISTENING so the
    avatar reflects that the mic is open for a follow-up."""
    bus = EventBus()
    tts = FakeTTS()
    supervisor = Supervisor(bus=bus)
    player = StateProbePlayer(supervisor=supervisor)
    pipeline = _make_pipeline(tts, bus)
    pipeline._supervisor = supervisor  # type: ignore[assignment]
    pipeline._player = player  # type: ignore[assignment]

    states: list[str] = []
    bus.subscribe(SystemStateChanged, lambda ev: states.append(ev.new_state))

    await bus.publish(
        AnnouncementRequested(text="Erledigt.", language="de", kind="subagent")
    )

    assert player.state_during_play == "SPEAKING"
    assert "SPEAKING" in states
    assert supervisor.state == "LISTENING"  # present → mic stays open


@pytest.mark.asyncio
async def test_readback_restores_to_idle_after_hangup() -> None:
    """If the user already hung up, the readback still animates but restores to
    IDLE — no surprising 'mic open' signal after a deliberate hangup."""
    bus = EventBus()
    tts = FakeTTS()
    supervisor = Supervisor(bus=bus)
    player = StateProbePlayer(supervisor=supervisor)
    pipeline = _make_pipeline(tts, bus)
    pipeline._supervisor = supervisor  # type: ignore[assignment]
    pipeline._player = player  # type: ignore[assignment]
    pipeline._hangup_event.set()  # type: ignore[attr-defined]

    await bus.publish(
        AnnouncementRequested(text="Erledigt.", language="de", kind="subagent")
    )

    assert player.state_during_play == "SPEAKING"
    assert supervisor.state == "IDLE"


@pytest.mark.asyncio
async def test_preamble_does_not_animate_speaking() -> None:
    """Only readbacks animate — a preamble keeps its prior visual (it fires
    during THINKING; flipping it to SPEAKING/LISTENING would be wrong)."""
    bus = EventBus()
    tts = FakeTTS()
    supervisor = Supervisor(bus=bus)
    player = StateProbePlayer(supervisor=supervisor)
    pipeline = _make_pipeline(tts, bus)
    pipeline._supervisor = supervisor  # type: ignore[assignment]
    pipeline._player = player  # type: ignore[assignment]

    states: list[str] = []
    bus.subscribe(SystemStateChanged, lambda ev: states.append(ev.new_state))

    await bus.publish(
        AnnouncementRequested(
            text="Einen Moment, ich schaue nach.", language="de", kind="preamble"
        )
    )

    assert player.plays == 1  # it still speaks
    assert "SPEAKING" not in states  # but does not drive the speaking state


# --------------------------------------------------------------------------
# Task 4 — OpenClaw-background readback re-arms the keep-listening grace
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_background_completed_arms_readback_grace() -> None:
    """The OpenClaw-background DIRECT path plays straight to the player (not via
    _on_announcement), so it must stamp the readback-grace timestamp itself —
    otherwise _active_session idle-times-out seconds after the result."""
    bus = EventBus()
    tts = FakeTTS()
    player = FakePlayer()
    pipeline = _make_pipeline(tts, bus, player)
    assert pipeline._last_announcement_spoken_monotonic is None  # type: ignore[attr-defined]

    await bus.publish(
        JarvisAgentBackgroundCompleted(
            success=True,
            utterance="recherchier mir fuenf themen",
            summary="Fuenf Recherche-Themen liegen bereit.",
            error="",
            duration_s=12.3,
        )
    )

    assert player.plays == 1
    assert pipeline._last_announcement_spoken_monotonic is not None  # type: ignore[attr-defined]
