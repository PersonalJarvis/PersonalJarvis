"""Gates added by the 2026-07-06 interim-ack redesign.

Forensic trigger (voice session 2026-07-05 19:47): three user utterances in one
session each spoke the byte-identical grounded ack — repetitive chatter. Two
speech-layer defenses are pinned here:

* **Duplicate-wording safety net** — a preamble/progress announcement whose
  scrubbed text matches the previously spoken preamble/progress line within
  ``preamble_dedup_window_s`` is dropped, regardless of which emitter sent it.
  Completion/interrupt readbacks are exempt (they deliver owed answers).
* **Grounded-ack usefulness gate** — a ``source_layer="brain.router.ack"``
  preamble arriving while the voice turn is PROCESSING only speaks if the
  brain is STILL busy after ``grounded_ack_commit_grace_ms`` (AD-OE5 helper);
  a turn that answers within the grace stays ack-free.

Spec: docs/superpowers/specs/2026-07-06-interim-ack-redesign-design.md
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from types import SimpleNamespace

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
        self.stop_calls += 1


def _make_pipeline(
    tts: FakeTTS,
    bus: EventBus,
    player: FakePlayer,
    *,
    commit_grace_ms: int = 900,
    dedup_window_s: int = 180,
    rate_limit_per_min: int = 3,
) -> SpeechPipeline:
    pipeline = SpeechPipeline(tts=tts, bus=bus, enable_whisper_wake=False)
    pipeline._player = player  # type: ignore[assignment]
    pipeline._config = SimpleNamespace(  # type: ignore[attr-defined]
        ack_brain=SimpleNamespace(
            suppress_preamble_after_interrupt_ms=5000,
            grounded_ack_commit_grace_ms=commit_grace_ms,
            preamble_dedup_window_s=dedup_window_s,
            preamble_rate_limit_per_min=rate_limit_per_min,
        )
    )
    return pipeline


def _preamble(text: str, *, source_layer: str = "") -> AnnouncementRequested:
    return AnnouncementRequested(
        text=text,
        language="de",
        priority="normal",
        kind="preamble",
        source_layer=source_layer,
    )


# ---------------------------------------------------------------------------
# Duplicate-wording safety net
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_identical_preamble_wording_is_dropped() -> None:
    """The forensic bug: the SAME interim line spoken repeatedly. The second
    identical preamble inside the dedup window must be silent."""
    bus = EventBus()
    tts = FakeTTS()
    player = FakePlayer()
    _make_pipeline(tts, bus, player)

    await bus.publish(_preamble("Ich schaue kurz nach."))
    await bus.publish(_preamble("Ich schaue kurz nach."))

    assert player.plays == 1, "the identical preamble wording spoke twice"
    assert len(tts.calls) == 1


@pytest.mark.asyncio
async def test_different_preamble_wording_still_plays() -> None:
    bus = EventBus()
    tts = FakeTTS()
    player = FakePlayer()
    _make_pipeline(tts, bus, player)

    await bus.publish(_preamble("Ich schaue kurz nach."))  # i18n-allow: German TTS fixture
    await bus.publish(_preamble("Einen Moment, ich sehe nach."))  # i18n-allow: German TTS fixture

    assert player.plays == 2, "a varied preamble was wrongly deduped"


@pytest.mark.asyncio
async def test_dedup_disabled_restores_legacy_behavior() -> None:
    bus = EventBus()
    tts = FakeTTS()
    player = FakePlayer()
    _make_pipeline(tts, bus, player, dedup_window_s=0)

    await bus.publish(_preamble("Ich schaue kurz nach."))
    await bus.publish(_preamble("Ich schaue kurz nach."))

    assert player.plays == 2


@pytest.mark.asyncio
async def test_completion_readbacks_are_never_deduped() -> None:
    """A completion delivers an owed answer — identical wording (e.g. two
    missions finishing with the same summary) must still be spoken."""
    bus = EventBus()
    tts = FakeTTS()
    player = FakePlayer()
    _make_pipeline(tts, bus, player)

    for _ in range(2):
        await bus.publish(
            AnnouncementRequested(
                text="Fertig. Das Ergebnis liegt bereit.",  # i18n-allow: German TTS fixture
                language="de",
                priority="normal",
                kind="completion",
            )
        )

    assert player.plays == 2, "an owed completion readback was deduped"


# ---------------------------------------------------------------------------
# Anti-loop rate-limit backstop (v2)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rate_limit_caps_preambles_per_minute() -> None:
    """The historical 'kept saying it forever' loop dies here: beyond the
    per-minute cap, further preamble-class lines are dropped — even with
    DISTINCT wording (dedup alone would not catch a looping emitter that
    varies its text)."""
    bus = EventBus()
    tts = FakeTTS()
    player = FakePlayer()
    _make_pipeline(tts, bus, player, rate_limit_per_min=3)

    for i in range(5):
        await bus.publish(_preamble(f"Meldung Nummer {i}."))  # i18n-allow: German fixture

    assert player.plays == 3, "the rate-limit backstop did not cap the loop"


@pytest.mark.asyncio
async def test_rate_limit_zero_disables_the_backstop() -> None:
    bus = EventBus()
    tts = FakeTTS()
    player = FakePlayer()
    _make_pipeline(tts, bus, player, rate_limit_per_min=0)

    for i in range(5):
        await bus.publish(_preamble(f"Meldung Nummer {i}."))  # i18n-allow: German fixture

    assert player.plays == 5


@pytest.mark.asyncio
async def test_rate_limit_never_touches_completions() -> None:
    """Completion readbacks are owed answers — a burst of finished missions
    must all be spoken regardless of the preamble cap."""
    bus = EventBus()
    tts = FakeTTS()
    player = FakePlayer()
    _make_pipeline(tts, bus, player, rate_limit_per_min=1)

    for i in range(3):
        await bus.publish(
            AnnouncementRequested(
                text=f"Auftrag {i} ist fertig geworden.",  # i18n-allow: German TTS fixture
                language="de",
                priority="normal",
                kind="completion",
            )
        )

    assert player.plays == 3


# ---------------------------------------------------------------------------
# Grounded-ack usefulness gate (commit grace)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_grounded_ack_dropped_when_answer_arrives_during_grace() -> None:
    """A turn that answers within the commit grace stays ack-free — the ack
    exists to bridge a LONG silence, not to precede a fast answer."""
    bus = EventBus()
    tts = FakeTTS()
    player = FakePlayer()
    pipeline = _make_pipeline(tts, bus, player, commit_grace_ms=200)
    pipeline._turn_state = TurnTakingState.PROCESSING  # type: ignore[attr-defined]

    async def _answer_quickly() -> None:
        await asyncio.sleep(0.02)
        pipeline._turn_state = TurnTakingState.JARVIS_SPEAKING  # type: ignore[attr-defined]

    flip = asyncio.create_task(_answer_quickly())
    await bus.publish(
        _preamble("Ich schaue kurz nach.", source_layer="brain.router.ack")
    )
    await flip

    assert player.plays == 0, (
        "the grounded ack spoke although the answer arrived within the grace"
    )


@pytest.mark.asyncio
async def test_grounded_ack_speaks_when_brain_stays_busy() -> None:
    bus = EventBus()
    tts = FakeTTS()
    player = FakePlayer()
    pipeline = _make_pipeline(tts, bus, player, commit_grace_ms=60)
    pipeline._turn_state = TurnTakingState.PROCESSING  # type: ignore[attr-defined]

    await bus.publish(
        _preamble("Ich schaue kurz nach.", source_layer="brain.router.ack")
    )

    assert player.plays == 1, "a genuinely slow turn lost its bridging ack"


@pytest.mark.asyncio
async def test_grounded_ack_without_voice_turn_keeps_legacy_path() -> None:
    """No voice turn in flight (turn-state IDLE — e.g. a chat-originated
    tool turn): the commit grace must NOT newly suppress the ack."""
    bus = EventBus()
    tts = FakeTTS()
    player = FakePlayer()
    _make_pipeline(tts, bus, player, commit_grace_ms=200)
    # Default turn-state is IDLE.

    await bus.publish(
        _preamble("Ich schaue kurz nach.", source_layer="brain.router.ack")
    )

    assert player.plays == 1


@pytest.mark.asyncio
async def test_flash_brain_preamble_is_not_double_gated() -> None:
    """The Flash-Brain preamble (source_layer="brain.ack_brain") already ran
    its own grace before publishing — the router-ack commit grace must not
    apply to it (no second delay, no drop)."""
    bus = EventBus()
    tts = FakeTTS()
    player = FakePlayer()
    pipeline = _make_pipeline(tts, bus, player, commit_grace_ms=10_000)
    pipeline._turn_state = TurnTakingState.PROCESSING  # type: ignore[attr-defined]

    await asyncio.wait_for(
        bus.publish(
            _preamble("Lass mich kurz nachschauen.", source_layer="brain.ack_brain")
        ),
        timeout=2.0,
    )

    assert player.plays == 1
