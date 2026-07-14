"""Unit tests for the TTS announcement bridge (Phase 5 CL-13).

The ``AnnouncementRequested`` event is emitted by the RouterBrain / tools
when the user should be given a concrete interim announcement without going
through the brain path. The ``SpeechPipeline`` subscribes to it and plays the
announcement via ``synthesize()`` + ``player.play_chunks()`` — the identical
path as normal answers.

Regression background (2026-04-23): the earlier tests here ran against
a ``FakeTTS`` with a ``speak()`` method. The real ``GeminiFlashTTS``
only exposes ``synthesize()`` though, and the old ``_on_announcement``
called ``self._tts.speak(...)`` — a silent ``AttributeError`` that made
announcements inaudible for months. The tests were green because the
fake and the implementation confirmed each other. Now it is tested
against the real ``synthesize()`` API.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.events import (
    AnnouncementRequested,
    JarvisAgentAnnouncement,
    JarvisAgentBackgroundCompleted,
)
from jarvis.core.protocols import AudioChunk
from jarvis.speech.pipeline import SpeechPipeline


@dataclass
class FakeTTS:
    """TTS fake with a real ``synthesize`` API (protocol-conformant)."""

    name: str = "fake-tts"
    supports_streaming: bool = True
    calls: list[tuple[str, str | None]] = field(default_factory=list)

    async def synthesize(
        self, text: str, voice: str | None = None,
        language_code: str | None = None,
    ) -> AsyncIterator[AudioChunk]:
        self.calls.append((text, language_code))
        # AsyncGenerator with no yields → empty stream, enough for the assertions
        if False:  # pragma: no cover
            yield  # type: ignore[unreachable]


@dataclass
class FakePlayer:
    """Player fake: records play_chunks calls + stop calls."""

    stop_calls: int = 0
    plays: int = 0
    order: list[str] = field(default_factory=list)

    async def play_chunks(self, chunks: AsyncIterator[AudioChunk]) -> None:
        self.plays += 1
        self.order.append("play")
        async for _ in chunks:
            pass

    def stop(self) -> None:
        self.stop_calls += 1
        self.order.append("stop")


class FakeBrain:
    """Counts calls — must NEVER be called for announcements."""

    def __init__(self) -> None:
        self.calls: int = 0

    async def __call__(self, *args, **kwargs) -> str:  # noqa: ANN002, ANN003
        self.calls += 1
        return ""


def _make_pipeline(
    tts: FakeTTS, bus: EventBus, player: FakePlayer | None = None
) -> SpeechPipeline:
    """Pipeline wired with a fake TTS + bus + optional FakePlayer.

    STT/wake are irrelevant here. The FakePlayer replaces ``self._player``,
    so no real WASAPI gets opened.
    """
    pipeline = SpeechPipeline(tts=tts, bus=bus, enable_whisper_wake=False)
    if player is not None:
        pipeline._player = player  # type: ignore[assignment]
    return pipeline


@pytest.mark.asyncio
async def test_announcement_event_triggers_synthesize() -> None:
    """``AnnouncementRequested`` → pipeline ruft ``tts.synthesize(...)``."""
    bus = EventBus()
    tts = FakeTTS()
    player = FakePlayer()
    _make_pipeline(tts, bus, player)

    await bus.publish(
        AnnouncementRequested(text="hallo welt", language="de", priority="normal")
    )

    assert tts.calls == [("hallo welt", "de-DE")]
    assert player.plays == 1


@pytest.mark.asyncio
async def test_announcement_english_language_passthrough() -> None:
    """The language parameter is mapped to language_code (en → en-US).

    Audit F-AUDIT-5 (2026-04-29): migrated the test text to a neutral phrase,
    because scrub_for_voice scrubs "sir" address + "sub-agent" engineering
    compounds (mandate A1). The test verifies the bridge mechanics, not
    the phrase contents.
    """
    bus = EventBus()
    tts = FakeTTS()
    player = FakePlayer()
    _make_pipeline(tts, bus, player)

    await bus.publish(
        AnnouncementRequested(text="one moment please", language="en")
    )

    assert tts.calls == [("one moment please", "en-US")]


@pytest.mark.asyncio
async def test_accepted_realtime_call_never_falls_through_to_classic_tts() -> None:
    class _RejectedRealtimeDelivery:
        # The provider may already have reported a terminal error, but the
        # accepted call still owns the output surface until lifecycle teardown.
        is_active = False

        def __init__(self) -> None:
            self.calls = 0

        async def deliver_announcement(self, **_kwargs) -> bool:  # noqa: ANN003
            self.calls += 1
            return False

    bus = EventBus()
    tts = FakeTTS()
    player = FakePlayer()
    pipeline = _make_pipeline(tts, bus, player)
    realtime = _RejectedRealtimeDelivery()
    pipeline._active_voice_mode = "realtime"
    pipeline._active_realtime_handle = realtime

    await bus.publish(
        AnnouncementRequested(
            text="I am gathering the detailed information now.",
            language="en",
            kind="preamble",
        )
    )

    assert realtime.calls == 1
    assert tts.calls == []
    assert player.plays == 0


@pytest.mark.asyncio
async def test_announcement_bypass_skips_brain() -> None:
    """Announcements must not touch the brain path (TTS bypass).

    Audit F-AUDIT-5: replaced "sub-agent" with the neutral "routine" — the
    test verifies that FakeBrain is not touched, not the
    phrase content.
    """
    bus = EventBus()
    tts = FakeTTS()
    player = FakePlayer()
    brain = FakeBrain()
    pipeline = _make_pipeline(tts, bus, player)
    pipeline._brain = brain  # type: ignore[assignment]

    await bus.publish(
        AnnouncementRequested(text="starte die routine", language="de")
    )

    assert brain.calls == 0
    assert tts.calls == [("starte die routine", "de-DE")]


@pytest.mark.asyncio
async def test_announcement_interrupt_calls_player_stop_before_play() -> None:
    """With ``priority="interrupt"``, ``player.stop()`` is called first,
    then ``synthesize()`` + ``play_chunks()`` — in that order."""
    bus = EventBus()
    tts = FakeTTS()
    player = FakePlayer()
    _make_pipeline(tts, bus, player)

    await bus.publish(
        AnnouncementRequested(
            text="achtung, abbruch", language="de", priority="interrupt"  # i18n-allow: simulated German announcement voice output under test
        )
    )

    assert player.stop_calls == 1
    assert tts.calls == [("achtung, abbruch", "de-DE")]  # i18n-allow: matches simulated German voice output above
    assert player.order == ["stop", "play"]


@pytest.mark.asyncio
async def test_announcement_normal_does_not_call_stop() -> None:
    """Bei ``priority="normal"`` bleibt ``player.stop()`` unangetastet."""
    bus = EventBus()
    tts = FakeTTS()
    player = FakePlayer()
    _make_pipeline(tts, bus, player)

    await bus.publish(
        AnnouncementRequested(text="kurzer hinweis", language="de", priority="normal")
    )

    assert player.stop_calls == 0
    assert tts.calls == [("kurzer hinweis", "de-DE")]


@pytest.mark.asyncio
async def test_announcement_without_bus_is_safe() -> None:
    """Without a bus, no subscription is created — the pipeline stays functional."""
    tts = FakeTTS()
    pipeline = SpeechPipeline(tts=tts, bus=None, enable_whisper_wake=False)
    assert pipeline._bus is None
    assert tts.calls == []


@pytest.mark.asyncio
async def test_openclaw_spawn_announcement_is_silent() -> None:
    """``JarvisAgentAnnouncement`` must NOT speak via TTS anymore.

    Spawn-ACK history:
    - 2026-04-25 .. 2026-05-10: silent (user request).
    - 2026-05-11: briefly reactivated with "Okay, mache ich." so the
      user could tell a spawn apart from a silent timeout.
    - 2026-05-12 (current): user revokes it again — the phrase is annoying.
      Spawn feedback is now handled by the Sub-Agents board (visual) and the
      background-completed readback at the end of the mission.

    Regression guard: if someone reintroduces the ACK without an ADR update,
    this test catches it.
    """
    from uuid import uuid4

    bus = EventBus()
    tts = FakeTTS()
    player = FakePlayer()
    _make_pipeline(tts, bus, player)

    await bus.publish(
        JarvisAgentAnnouncement(
            trace_id=uuid4(),
            action="the workflow described by the user",
            target="",
        )
    )

    assert tts.calls == [], (
        f"Spawn ACK must not speak, but TTS was called: "
        f"{tts.calls!r}"
    )
    assert player.plays == 0


@pytest.mark.asyncio
async def test_openclaw_background_success_with_summary_speaks() -> None:
    """``JarvisAgentBackgroundCompleted(success=True, summary=...)`` → Voice-Ansage."""
    bus = EventBus()
    tts = FakeTTS()
    player = FakePlayer()
    _make_pipeline(tts, bus, player)

    await bus.publish(
        JarvisAgentBackgroundCompleted(
            success=True,
            utterance="recherchier mir fuenf themen",  # i18n-allow: simulated German user utterance under test
            summary="Fuenf Recherche-Themen liegen bereit.",  # i18n-allow: simulated German voice output under test
            error="",
            duration_s=12.3,
        )
    )

    assert tts.calls == [
        ("Fertig. Fuenf Recherche-Themen liegen bereit.", "de-DE")  # i18n-allow: matches simulated German voice output above
    ]
    assert player.plays == 1


@pytest.mark.asyncio
async def test_openclaw_background_success_no_summary_speaks_fertig() -> None:  # i18n-allow: identifier name, not translatable prose
    """Success with no summary → ``"Fertig."`` (output-filter-safe)."""  # i18n-allow: quotes the actual German TTS output under test
    bus = EventBus()
    tts = FakeTTS()
    player = FakePlayer()
    _make_pipeline(tts, bus, player)

    await bus.publish(
        JarvisAgentBackgroundCompleted(
            success=True, utterance="mach mir das", summary="", error="", duration_s=1.0,
        )
    )

    assert tts.calls == [("Fertig.", "de-DE")]  # i18n-allow: simulated German voice output under test
    assert player.plays == 1


@pytest.mark.asyncio
async def test_openclaw_background_failure_speaks_error() -> None:
    """``success=False`` → error announcement ``"Das hat nicht geklappt. ..."``."""  # i18n-allow: quotes the actual German TTS output under test
    bus = EventBus()
    tts = FakeTTS()
    player = FakePlayer()
    _make_pipeline(tts, bus, player)

    await bus.publish(
        JarvisAgentBackgroundCompleted(
            success=False, utterance="mach mir das", summary="",
            error="rate-limit reached", duration_s=2.0,
        )
    )

    # rate-limit survives scrub_for_voice. "Provider" would be scrubbed.
    # event.error ends without a period → text ends without a period (simple concat).
    assert tts.calls == [("Das hat nicht geklappt. rate-limit reached", "de-DE")]  # i18n-allow: simulated German voice output under test
    assert player.plays == 1


@pytest.mark.asyncio
async def test_announcement_regression_no_speak_api() -> None:
    """Regression guard: the pipeline must not call ``tts.speak(...)``.

    The FakeTTS deliberately does NOT expose ``speak``. If the pipeline
    still tried to call ``tts.speak(...)`` (as in the 2026-04-23 bug
    version), the ``AttributeError`` landed silently in the except block and
    the spy on ``synthesize()`` would be empty.
    """
    bus = EventBus()
    tts = FakeTTS()
    assert not hasattr(tts, "speak"), "fake must NOT expose speak()"
    player = FakePlayer()
    _make_pipeline(tts, bus, player)

    await bus.publish(
        AnnouncementRequested(text="zu diensten alex", language="de")
    )

    assert tts.calls == [("zu diensten alex", "de-DE")], (
        "announcement must go through synthesize(), not tts.speak()"
    )
    assert player.plays == 1


@pytest.mark.asyncio
async def test_announcement_language_none_resolves_via_conversation() -> None:
    """A ``language=None`` event must NOT pass ``language_code=None`` to TTS.

    Forensic 2026-06-23: a None/auto language_code lets the multilingual TTS
    (Cartesia) fall back to its English voice on German text. The announcement
    handler now resolves the language through ``_output_language``, so an
    untagged announcement follows the established conversation language (here
    German) instead of leaking ``None``.
    """
    from types import SimpleNamespace

    bus = EventBus()
    tts = FakeTTS()
    player = FakePlayer()
    pipe = _make_pipeline(tts, bus, player)
    pipe._brain = SimpleNamespace(reply_language="auto", conversation_language="de")

    await bus.publish(
        AnnouncementRequested(text="test", language=None)
    )

    assert tts.calls == [("test", "de-DE")]


@pytest.mark.asyncio
async def test_empty_announcement_is_not_spoken() -> None:
    """Empty announcements are UI/state signals, not TTS phrases."""
    bus = EventBus()
    tts = FakeTTS()
    player = FakePlayer()
    _make_pipeline(tts, bus, player)

    await bus.publish(AnnouncementRequested(text="", language="de"))

    assert tts.calls == []
    assert player.plays == 0


@pytest.mark.asyncio
async def test_openclaw_spawn_action_echo_is_not_spoken() -> None:
    """The spawn voice path has been completely silent since 2026-05-12.

    History of this test:
    - Pre-2026-05-11: verified the path doesn't speak at all (original suppress).
    - 2026-05-11: spawn ACK reactivated, test verifies a FIXED phrase without
      echoing ``event.action`` (tool-use-leak vector).
    - 2026-05-12: user revokes the ACK entirely. Test guarantees:
      no matter what action/target is on the event, NO TTS call.
    """
    from uuid import uuid4

    bus = EventBus()
    tts = FakeTTS()
    player = FakePlayer()
    _make_pipeline(tts, bus, player)

    await bus.publish(
        JarvisAgentAnnouncement(
            trace_id=uuid4(),
            action="builds an app",
            target="in the workspace",
        )
    )

    assert tts.calls == [], (
        f"Spawn path must not speak, but TTS was called: "
        f"{tts.calls!r}"
    )
    assert player.plays == 0


@pytest.mark.asyncio
async def test_openclaw_completion_signal_does_speak_with_summary() -> None:
    """The completion voice message MUST speak — user request 2026-05-11.

    Before 2026-05-11 this path was suppressed and the predecessor test
    verified ``tts.calls == []``. Today's regression guard is for the
    opposite behavior: success+summary → short voice announcement.
    """
    bus = EventBus()
    tts = FakeTTS()
    player = FakePlayer()
    _make_pipeline(tts, bus, player)

    await bus.publish(
        JarvisAgentBackgroundCompleted(
            success=True,
            utterance="baue mir etwas",
            summary="Fertig gebaut",  # i18n-allow: simulated German voice output under test
            duration_s=1.2,
        )
    )

    assert tts.calls == [("Fertig. Fertig gebaut", "de-DE")]  # i18n-allow: matches simulated German voice output above
    assert player.plays == 1


# ---------------------------------------------------------------------------
# Late mission readback must survive a hang-up (live bug 2026-06-14): once a
# heavy research request is OFFLOADED to a background mission, its result often
# arrives AFTER the user said "auflegen". A fresh kind="completion" readback is
# the answer the user asked for — it must punch through the hangup gate (a NEW
# turn, AD-OE5/OE6), while a stale preamble / plain late announcement stays
# correctly dropped. priority="normal" keeps it queued behind current speech.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_completion_announcement_speaks_after_hangup() -> None:
    """A kind="completion" announcement must be SPOKEN even after hangup — it is
    the offloaded answer, a fresh turn. RED before WS3b (the hangup gate drops
    every announcement regardless of kind)."""
    bus = EventBus()
    tts = FakeTTS()
    player = FakePlayer()
    pipeline = _make_pipeline(tts, bus, player)
    pipeline._hangup_event.set()  # type: ignore[attr-defined]

    await bus.publish(
        AnnouncementRequested(
            text="Deine Reise-Recherche ist fertig.",  # i18n-allow: simulated German voice output under test
            language="de",
            priority="normal",
            kind="completion",
        )
    )

    assert tts.calls == [("Deine Reise-Recherche ist fertig.", "de-DE")]  # i18n-allow: matches simulated German voice output above
    assert player.plays == 1


@pytest.mark.asyncio
async def test_stale_preamble_dropped_after_hangup() -> None:
    """A stale kind="preamble" announcement after hangup stays dropped (it is a
    leftover from the aborted turn, not the answer). Guard for WS3b."""
    bus = EventBus()
    tts = FakeTTS()
    player = FakePlayer()
    pipeline = _make_pipeline(tts, bus, player)
    pipeline._hangup_event.set()  # type: ignore[attr-defined]

    await bus.publish(
        AnnouncementRequested(
            text="Einen Moment, ich schaue nach.",  # i18n-allow: simulated German voice output under test
            language="de",
            priority="normal",
            kind="preamble",
        )
    )

    assert tts.calls == []
    assert player.plays == 0


@pytest.mark.asyncio
async def test_plain_late_announcement_still_dropped_after_hangup() -> None:
    """An untagged (kind=None) late announcement after hangup stays dropped —
    only a deliberate completion punches through. Guard for WS3b."""
    bus = EventBus()
    tts = FakeTTS()
    player = FakePlayer()
    pipeline = _make_pipeline(tts, bus, player)
    pipeline._hangup_event.set()  # type: ignore[attr-defined]

    await bus.publish(
        AnnouncementRequested(text="irgendeine späte ansage", language="de")  # i18n-allow: simulated German voice output under test
    )

    assert tts.calls == []
    assert player.plays == 0


@pytest.mark.asyncio
async def test_background_completed_speaks_after_hangup() -> None:
    """An JarvisAgentBackgroundCompleted readback is by definition fresh — it must
    be spoken even after hangup. RED before WS3b (the hangup gate dropped it)."""
    bus = EventBus()
    tts = FakeTTS()
    player = FakePlayer()
    pipeline = _make_pipeline(tts, bus, player)
    pipeline._hangup_event.set()  # type: ignore[attr-defined]

    await bus.publish(
        JarvisAgentBackgroundCompleted(
            success=True,
            utterance="recherchier mir fuenf themen",  # i18n-allow: simulated German user utterance under test
            summary="Fuenf Recherche-Themen liegen bereit.",  # i18n-allow: simulated German voice output under test
            error="",
            duration_s=12.3,
        )
    )

    assert tts.calls == [
        ("Fertig. Fuenf Recherche-Themen liegen bereit.", "de-DE")  # i18n-allow: matches simulated German voice output above
    ]
    assert player.plays == 1


@pytest.mark.asyncio
async def test_completion_announcement_still_silenced_when_muted() -> None:
    """Mute is the user's explicit choice and stays above the completion gate:
    a muted completion is silent (the result is still visible in the UI)."""
    bus = EventBus()
    tts = FakeTTS()
    player = FakePlayer()
    pipeline = _make_pipeline(tts, bus, player)
    pipeline._muted = True  # type: ignore[attr-defined]

    await bus.publish(
        AnnouncementRequested(
            text="Deine Reise-Recherche ist fertig.",  # i18n-allow: simulated German voice output under test
            language="de",
            kind="completion",
        )
    )

    assert tts.calls == []
    assert player.plays == 0
