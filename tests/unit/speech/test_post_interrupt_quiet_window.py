"""Post-interrupt quiet-window for preamble announcements.

Diagnosis source: ``docs/plans/voice-phrase-mismatch-2026-05-26/README.md``.

The user heard a coherent-looking but semantically-incoherent voice block
on 2026-05-26:

    "Die Mission ist fehlgeschlagen, drei Versuche haben nicht gereicht.  # i18n-allow: quotes the actual German TTS output under test
     Dann schauen wir einfach mal in die letzte Transkription, was ich
     gesagt habe."  # i18n-allow

The first sentence is the deterministic ``MissionFailed`` voice readback
emitted by ``MissionAnnouncer`` with ``priority="interrupt"`` (see
``jarvis/missions/voice/announcer.py:178+209``).  The second sentence
matches the shape of a Pre-Thinking-Ack Flash-Brain preamble — a
mission-completion / skill announcement / any other surface that
publishes ``AnnouncementRequested(kind="preamble", ...)``.

Behavioural contract enforced here: after the pipeline plays a
``priority="interrupt"`` announcement, **no preamble-class announcement
is permitted in the next ``suppress_preamble_after_interrupt_ms``
milliseconds**.  Completion/info announcements, and the
interrupt-priority announcement itself, are *not* affected — only the
"I'm about to think about this"-class chatter that becomes nonsensical
once a mission-failure has just landed in the user's ear.

This is a structural defence against H3 + H4 from the diagnosis README:
several distinct preamble-emitting subscribers exist on
``AnnouncementRequested``; a per-subscriber fix would not generalise.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.events import AnnouncementRequested
from jarvis.core.protocols import AudioChunk
from jarvis.speech.pipeline import SpeechPipeline


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

    async def play_chunks(self, chunks: AsyncIterator[AudioChunk]) -> None:
        self.plays += 1
        async for _ in chunks:
            pass

    def stop(self) -> None:
        self.stop_calls += 1


def _make_pipeline(
    tts: FakeTTS,
    bus: EventBus,
    player: FakePlayer,
) -> SpeechPipeline:
    """SpeechPipeline wired with fakes — no real audio I/O."""
    pipeline = SpeechPipeline(tts=tts, bus=bus, enable_whisper_wake=False)
    pipeline._player = player  # type: ignore[assignment]
    return pipeline


class _FakeClock:
    """Monotonic-clock fake — tests advance time deterministically."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def fake_clock(monkeypatch: pytest.MonkeyPatch) -> _FakeClock:
    clock = _FakeClock()
    monkeypatch.setattr("jarvis.speech.pipeline.time.monotonic", clock)
    return clock


@pytest.mark.asyncio
async def test_preamble_within_quiet_window_after_interrupt_is_suppressed(
    fake_clock: _FakeClock,
) -> None:
    """Preamble that arrives within the quiet window after an interrupt is dropped.

    Scenario from the 2026-05-26 incident: MissionAnnouncer publishes a
    ``MissionFailed`` readback with ``priority="interrupt"``.  Half a
    second later, the Flash-Brain ack coordinator publishes a preamble.
    The preamble must NOT reach TTS — that is the cross-surface voice
    incoherence the diagnosis README names.
    """
    bus = EventBus()
    tts = FakeTTS()
    player = FakePlayer()
    _make_pipeline(tts, bus, player)

    await bus.publish(
        AnnouncementRequested(
            text="Die Mission ist fehlgeschlagen. Drei Versuche haben nicht gereicht.",  # i18n-allow
            language="de",
            priority="interrupt",
        )
    )
    # 0.5 s later — well inside the 5 s default quiet window.
    fake_clock.advance(0.5)
    await bus.publish(
        AnnouncementRequested(
            text="Lass mich kurz nachschauen.",
            language="de",
            priority="normal",
            kind="preamble",
        )
    )

    # Only the interrupt announcement spoke.  The preamble was suppressed.
    assert tts.calls == [
        (
            "Die Mission ist fehlgeschlagen. Drei Versuche haben nicht gereicht.",  # i18n-allow
            "de-DE",
        )
    ]
    assert player.plays == 1


@pytest.mark.asyncio
async def test_preamble_after_quiet_window_expires_is_spoken(
    fake_clock: _FakeClock,
) -> None:
    """Once the quiet window has elapsed, preambles return to normal behaviour."""
    bus = EventBus()
    tts = FakeTTS()
    player = FakePlayer()
    _make_pipeline(tts, bus, player)

    await bus.publish(
        AnnouncementRequested(
            text="Mission abgebrochen.",
            language="de",
            priority="interrupt",
        )
    )
    # 6 s later — past the 5 s default quiet window.
    fake_clock.advance(6.0)
    await bus.publish(
        AnnouncementRequested(
            text="Ich schaue gleich nach.",
            language="de",
            priority="normal",
            kind="preamble",
        )
    )

    assert tts.calls == [
        ("Mission abgebrochen.", "de-DE"),
        ("Ich schaue gleich nach.", "de-DE"),
    ]
    assert player.plays == 2


@pytest.mark.asyncio
async def test_completion_within_quiet_window_is_not_suppressed(
    fake_clock: _FakeClock,
) -> None:
    """Only ``kind == "preamble"`` is gated — completion readbacks pass through.

    A completion (e.g. ``JarvisAgentBackgroundCompleted`` → "Fertig.") that  # i18n-allow
    lands inside the quiet window after a mission failure on another
    track must still be voiced.  Suppressing it would lose information
    the user actually needs.
    """
    bus = EventBus()
    tts = FakeTTS()
    player = FakePlayer()
    _make_pipeline(tts, bus, player)

    await bus.publish(
        AnnouncementRequested(
            text="Mission abgebrochen.",
            language="de",
            priority="interrupt",
        )
    )
    fake_clock.advance(0.2)
    await bus.publish(
        AnnouncementRequested(
            text="Fertig. Anderes Ergebnis liegt bereit.",  # i18n-allow
            language="de",
            priority="normal",
            kind="completion",
        )
    )

    assert tts.calls == [
        ("Mission abgebrochen.", "de-DE"),
        ("Fertig. Anderes Ergebnis liegt bereit.", "de-DE"),  # i18n-allow
    ]
    assert player.plays == 2


@pytest.mark.asyncio
async def test_interrupt_itself_is_always_spoken(
    fake_clock: _FakeClock,
) -> None:
    """The interrupt announcement that *opens* the quiet window must speak.

    Regression-guard: the bookkeeping update for
    ``_last_interrupt_announcement_ts`` must not block the very
    announcement that sets it.
    """
    bus = EventBus()
    tts = FakeTTS()
    player = FakePlayer()
    _make_pipeline(tts, bus, player)

    await bus.publish(
        AnnouncementRequested(
            text="Die Mission ist fehlgeschlagen. Grund: kritisch.",  # i18n-allow
            language="de",
            priority="interrupt",
        )
    )

    assert tts.calls == [
        ("Die Mission ist fehlgeschlagen. Grund: kritisch.", "de-DE"),  # i18n-allow
    ]
    assert player.plays == 1


@pytest.mark.asyncio
async def test_preamble_without_prior_interrupt_is_spoken(
    fake_clock: _FakeClock,
) -> None:
    """The first preamble of a fresh session is not gated.

    Without a prior interrupt the timestamp is ``None`` and the gate
    is inactive.  This is the common-case happy-path — the gate only
    arms once a real failure-class announcement has just landed.
    """
    bus = EventBus()
    tts = FakeTTS()
    player = FakePlayer()
    _make_pipeline(tts, bus, player)

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
