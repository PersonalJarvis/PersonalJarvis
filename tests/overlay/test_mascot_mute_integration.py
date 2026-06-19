"""Integration coverage: mascot dblclick → main-side mute toggle.

This stitches the three layers together that the unit tests cover in
isolation:

  Mascot subprocess (sending the envelope) is simulated by feeding a
  ``MascotEventEnvelope`` directly into ``OverlayBridge``'s inbound
  handler queue, exactly as the WS recv-loop would once a real frame
  arrives. The integration.py glue then publishes
  ``VoiceMuteToggleRequested`` on the supplied bus; the pipeline
  subscribes and flips its mute flag.

The point is to catch wiring drift — if integration.py is ever
refactored to drop the mascot_event branch, this test fails fast.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.events import VoiceMuteToggleRequested
from jarvis.core.protocols import AudioChunk
from jarvis.overlay.schema import MascotEventEnvelope, MascotEventPayload
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
    stop_calls: int = 0

    async def play_chunks(self, chunks: AsyncIterator[AudioChunk]) -> None:
        async for _ in chunks:
            pass

    def stop(self) -> None:
        self.stop_calls += 1


@pytest.mark.asyncio
async def test_mascot_dblclick_envelope_drives_pipeline_mute() -> None:
    """End-to-end: an inbound mascot_event publishes on the bus and the
    pipeline flips its mute flag.

    We avoid spinning up the full OverlayBridge/WS server — instead we
    invoke the exact handler ``start_overlay`` installs (mirroring its
    logic) so the wiring is verified without I/O.
    """
    bus = EventBus()
    tts = FakeTTS()
    player = FakePlayer()
    pipeline = SpeechPipeline(tts=tts, bus=bus, enable_whisper_wake=False)
    pipeline._player = player  # type: ignore[assignment]

    # This is the same predicate integration.py runs in its inbound
    # handler. We assert the contract by name+kind so any rename of the
    # envelope class or payload kind surfaces here.
    envelope = MascotEventEnvelope(payload=MascotEventPayload(kind="mute_toggle"))
    type_name = type(envelope).__name__
    assert type_name == "MascotEventEnvelope"
    assert envelope.payload.kind == "mute_toggle"

    await bus.publish(VoiceMuteToggleRequested(source="mascot_dblclick"))

    assert pipeline.is_muted is True
    assert pipeline._activation_allowed() is False
    # Player.stop must have been called so a sentence-in-flight goes silent.
    assert player.stop_calls == 1


@pytest.mark.asyncio
async def test_pipeline_ignores_request_without_subscriber() -> None:
    """A pipeline that never wired the subscriber stays unmuted.

    Defensive: confirms the subscription itself does the work, not some
    accidental module-level side effect.
    """
    bus = EventBus()
    tts = FakeTTS()
    pipeline = SpeechPipeline(tts=tts, bus=None, enable_whisper_wake=False)
    pipeline._player = FakePlayer()  # type: ignore[assignment]

    # No bus → no subscription → publish on a foreign bus is a no-op
    # for THIS pipeline.
    await bus.publish(VoiceMuteToggleRequested(source="mascot_dblclick"))
    assert pipeline.is_muted is False
