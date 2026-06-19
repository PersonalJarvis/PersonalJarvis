"""Unit-Tests fuer die TTS-Announcement-Bridge (Phase 5 CL-13).

Der ``AnnouncementRequested``-Event wird vom RouterBrain / Tools emittiert,
wenn dem User eine konkrete Zwischenansage ohne Brain-Pfad gegeben werden soll.
Die ``SpeechPipeline`` subscribed darauf und spielt die Ansage ueber ``synthesize()`` +
``player.play_chunks()`` ab — identischer Pfad wie normale Antworten.

Regression-Hintergrund (2026-04-23): Die frueheren Tests hier liefen gegen
einen ``FakeTTS`` mit ``speak()``-Methode. Der echte ``GeminiFlashTTS``
exponiert jedoch nur ``synthesize()``, und der alte ``_on_announcement``
rief ``self._tts.speak(...)`` auf — ein stummer ``AttributeError``, der
die Announcements monatelang unhoerbar machte. Die Tests waren grün, weil
Fake und Implementation sich gegenseitig bestaetigten. Jetzt wird gegen
die echte ``synthesize()``-API getestet.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.events import (
    AnnouncementRequested,
    OpenClawAnnouncement,
    OpenClawBackgroundCompleted,
)
from jarvis.core.protocols import AudioChunk
from jarvis.speech.pipeline import SpeechPipeline


@dataclass
class FakeTTS:
    """TTS-Fake mit realer ``synthesize``-API (Protokoll-konform)."""

    name: str = "fake-tts"
    supports_streaming: bool = True
    calls: list[tuple[str, str | None]] = field(default_factory=list)

    async def synthesize(
        self, text: str, voice: str | None = None,
        language_code: str | None = None,
    ) -> AsyncIterator[AudioChunk]:
        self.calls.append((text, language_code))
        # AsyncGenerator ohne yields → leerer Stream, reicht fuer Assertions
        if False:  # pragma: no cover
            yield  # type: ignore[unreachable]


@dataclass
class FakePlayer:
    """Player-Fake: zeichnet play_chunks-Aufrufe + stop-Calls auf."""

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
    """Zaehlt Calls — darf bei Announcements NIE gerufen werden."""

    def __init__(self) -> None:
        self.calls: int = 0

    async def __call__(self, *args, **kwargs) -> str:  # noqa: ANN002, ANN003
        self.calls += 1
        return ""


def _make_pipeline(
    tts: FakeTTS, bus: EventBus, player: FakePlayer | None = None
) -> SpeechPipeline:
    """Pipeline mit gestecktem Fake-TTS + Bus + optionalem FakePlayer.

    STT/Wake sind hier irrelevant. Der FakePlayer ersetzt ``self._player``,
    damit kein echtes WASAPI geoeffnet wird.
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
    """Sprachparameter wird zu language_code gemappt (en → en-US).

    Audit F-AUDIT-5 (2026-04-29): Test-Text auf neutrale Phrase migriert,
    weil scrub_for_voice "sir"-Anrede + "sub-agent"-Engineering-Compounds
    scrubbt (Mandat-A1). Der Test verifiziert die Bridge-Mechanik, nicht
    die Phrasen-Inhalte.
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
async def test_announcement_bypass_skips_brain() -> None:
    """Announcements duerfen den Brain-Pfad nicht anstossen (TTS-Bypass).

    Audit F-AUDIT-5: "sub-agent" durch neutrales "routine" ersetzt — der
    Test verifiziert dass FakeBrain nicht angestossen wird, nicht den
    Phrasen-Inhalt.
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
    """Bei ``priority="interrupt"`` wird erst ``player.stop()`` gerufen,
    dann ``synthesize()`` + ``play_chunks()`` — in dieser Reihenfolge."""
    bus = EventBus()
    tts = FakeTTS()
    player = FakePlayer()
    _make_pipeline(tts, bus, player)

    await bus.publish(
        AnnouncementRequested(
            text="achtung, abbruch", language="de", priority="interrupt"
        )
    )

    assert player.stop_calls == 1
    assert tts.calls == [("achtung, abbruch", "de-DE")]
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
    """Ohne Bus wird keine Subscription angelegt — Pipeline bleibt funktional."""
    tts = FakeTTS()
    pipeline = SpeechPipeline(tts=tts, bus=None, enable_whisper_wake=False)
    assert pipeline._bus is None
    assert tts.calls == []


@pytest.mark.asyncio
async def test_openclaw_spawn_announcement_is_silent() -> None:
    """``OpenClawAnnouncement`` darf NICHT mehr TTS-sprechen.

    Spawn-ACK-History:
    - 2026-04-25 .. 2026-05-10: stumm (User-Wunsch).
    - 2026-05-11: kurz reaktiviert mit "Okay, mache ich." damit der
      User Spawn von stillem Timeout unterscheiden kann.
    - 2026-05-12 (aktuell): User widerruft — die Phrase nervt. Spawn-
      Feedback uebernimmt das Sub-Agents-Board (visuell) und der
      Background-Completed-Readback am Ende der Mission.

    Regression-Guard: wenn jemand den ACK ohne ADR-Update wieder
    einbaut, schlaegt dieser Test an.
    """
    from uuid import uuid4

    bus = EventBus()
    tts = FakeTTS()
    player = FakePlayer()
    _make_pipeline(tts, bus, player)

    await bus.publish(
        OpenClawAnnouncement(
            trace_id=uuid4(),
            action="den vom User beschriebenen Workflow",
            target="",
        )
    )

    assert tts.calls == [], (
        f"Spawn-ACK darf nicht sprechen, aber TTS wurde gerufen: "
        f"{tts.calls!r}"
    )
    assert player.plays == 0


@pytest.mark.asyncio
async def test_openclaw_background_success_with_summary_speaks() -> None:
    """``OpenClawBackgroundCompleted(success=True, summary=...)`` → Voice-Ansage."""
    bus = EventBus()
    tts = FakeTTS()
    player = FakePlayer()
    _make_pipeline(tts, bus, player)

    await bus.publish(
        OpenClawBackgroundCompleted(
            success=True,
            utterance="recherchier mir fuenf themen",
            summary="Fuenf Recherche-Themen liegen bereit.",
            error="",
            duration_s=12.3,
        )
    )

    assert tts.calls == [
        ("Fertig. Fuenf Recherche-Themen liegen bereit.", "de-DE")
    ]
    assert player.plays == 1


@pytest.mark.asyncio
async def test_openclaw_background_success_no_summary_speaks_fertig() -> None:
    """Erfolg ohne Summary → ``"Fertig."`` (Output-Filter-safe)."""
    bus = EventBus()
    tts = FakeTTS()
    player = FakePlayer()
    _make_pipeline(tts, bus, player)

    await bus.publish(
        OpenClawBackgroundCompleted(
            success=True, utterance="x", summary="", error="", duration_s=1.0,
        )
    )

    assert tts.calls == [("Fertig.", "de-DE")]
    assert player.plays == 1


@pytest.mark.asyncio
async def test_openclaw_background_failure_speaks_error() -> None:
    """``success=False`` → Fehler-Ansage ``"Das hat nicht geklappt. ..."``."""
    bus = EventBus()
    tts = FakeTTS()
    player = FakePlayer()
    _make_pipeline(tts, bus, player)

    await bus.publish(
        OpenClawBackgroundCompleted(
            success=False, utterance="x", summary="",
            error="rate-limit reached", duration_s=2.0,
        )
    )

    # rate-limit ueberlebt scrub_for_voice. "Provider" wuerde gescrubbt.
    # event.error endet ohne Punkt → text endet ohne Punkt (simple Concat).
    assert tts.calls == [("Das hat nicht geklappt. rate-limit reached", "de-DE")]
    assert player.plays == 1


@pytest.mark.asyncio
async def test_announcement_regression_no_speak_api() -> None:
    """Regression-Guard: pipeline darf kein ``tts.speak(...)`` rufen.

    Der FakeTTS exponiert bewusst KEIN ``speak``. Wenn die Pipeline trotzdem
    versuchen wuerde ``tts.speak(...)`` zu rufen (wie in der 2026-04-23-Bug-
    Version), landete der ``AttributeError`` stumm im except-Block und der
    Spy auf ``synthesize()`` waere leer.
    """
    bus = EventBus()
    tts = FakeTTS()
    assert not hasattr(tts, "speak"), "Fake soll speak() NICHT exponieren"
    player = FakePlayer()
    _make_pipeline(tts, bus, player)

    await bus.publish(
        AnnouncementRequested(text="zu diensten ruben", language="de")
    )

    assert tts.calls == [("zu diensten ruben", "de-DE")], (
        "Announcement muss ueber synthesize() laufen, nicht tts.speak()"
    )
    assert player.plays == 1


@pytest.mark.asyncio
async def test_announcement_language_none_passes_none() -> None:
    """Wenn ``language=None`` im Event, wird auch kein language_code gesetzt."""
    bus = EventBus()
    tts = FakeTTS()
    player = FakePlayer()
    _make_pipeline(tts, bus, player)

    await bus.publish(
        AnnouncementRequested(text="test", language=None)
    )

    assert tts.calls == [("test", None)]


@pytest.mark.asyncio
async def test_empty_announcement_is_not_spoken() -> None:
    """Leere Announcements sind UI-/State-Signale, keine TTS-Phrasen."""
    bus = EventBus()
    tts = FakeTTS()
    player = FakePlayer()
    _make_pipeline(tts, bus, player)

    await bus.publish(AnnouncementRequested(text="", language="de"))

    assert tts.calls == []
    assert player.plays == 0


@pytest.mark.asyncio
async def test_openclaw_spawn_action_echo_is_not_spoken() -> None:
    """Spawn-Voice-Pfad ist seit 2026-05-12 komplett stumm.

    History dieses Tests:
    - Pre-2026-05-11: pruefte dass Pfad gar nicht spricht (urspruengl. Suppress).
    - 2026-05-11: Spawn-ACK reaktiviert, Test prueft FIXE Phrase ohne Echo
      aus ``event.action`` (Tool-Use-Leak-Vektor).
    - 2026-05-12: User widerruft den ACK komplett. Test garantiert:
      egal welche action/target im Event, KEIN TTS-Call.
    """
    from uuid import uuid4

    bus = EventBus()
    tts = FakeTTS()
    player = FakePlayer()
    _make_pipeline(tts, bus, player)

    await bus.publish(
        OpenClawAnnouncement(
            trace_id=uuid4(),
            action="eine App baut",
            target="im Workspace",
        )
    )

    assert tts.calls == [], (
        f"Spawn-Pfad darf nicht sprechen, aber TTS wurde gerufen: "
        f"{tts.calls!r}"
    )
    assert player.plays == 0


@pytest.mark.asyncio
async def test_openclaw_completion_signal_does_speak_with_summary() -> None:
    """Completion-Voice-Meldung MUSS sprechen — User-Wunsch 2026-05-11.

    Vor 2026-05-11 war dieser Pfad suppress't und der Vorgaenger-Test
    verifizierte ``tts.calls == []``. Heute Regression-Guard fuer das
    umgekehrte Verhalten: success+summary → kurze Voice-Ansage.
    """
    bus = EventBus()
    tts = FakeTTS()
    player = FakePlayer()
    _make_pipeline(tts, bus, player)

    await bus.publish(
        OpenClawBackgroundCompleted(
            success=True,
            utterance="baue etwas",
            summary="Fertig gebaut",
            duration_s=1.2,
        )
    )

    assert tts.calls == [("Fertig. Fertig gebaut", "de-DE")]
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
            text="Deine Reise-Recherche ist fertig.",
            language="de",
            priority="normal",
            kind="completion",
        )
    )

    assert tts.calls == [("Deine Reise-Recherche ist fertig.", "de-DE")]
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
            text="Einen Moment, ich schaue nach.",
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
        AnnouncementRequested(text="irgendeine späte ansage", language="de")
    )

    assert tts.calls == []
    assert player.plays == 0


@pytest.mark.asyncio
async def test_background_completed_speaks_after_hangup() -> None:
    """An OpenClawBackgroundCompleted readback is by definition fresh — it must
    be spoken even after hangup. RED before WS3b (the hangup gate dropped it)."""
    bus = EventBus()
    tts = FakeTTS()
    player = FakePlayer()
    pipeline = _make_pipeline(tts, bus, player)
    pipeline._hangup_event.set()  # type: ignore[attr-defined]

    await bus.publish(
        OpenClawBackgroundCompleted(
            success=True,
            utterance="recherchier mir fuenf themen",
            summary="Fuenf Recherche-Themen liegen bereit.",
            error="",
            duration_s=12.3,
        )
    )

    assert tts.calls == [
        ("Fertig. Fuenf Recherche-Themen liegen bereit.", "de-DE")
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
            text="Deine Reise-Recherche ist fertig.",
            language="de",
            kind="completion",
        )
    )

    assert tts.calls == []
    assert player.plays == 0
