"""``VoiceFactBridge.stop()`` must actually detach from the bus.

Regression guard for a silent leak: ``start()`` used to store the RETURN VALUE
of ``EventBus.subscribe``, which is ``None`` — so ``stop()`` iterated a list of
``None``, ``None()`` raised, a bare ``except Exception: pass`` swallowed it, and
the list was cleared with every handler still attached. Nothing failed loudly;
the bridge simply kept ingesting after it had been stopped, and a restart
stacked a second live set of handlers on top of the first.

The contract these tests pin is the observable one — what the bus holds, and
whether a handler still runs — not how the bridge stores its bookkeeping.
"""
from __future__ import annotations

import asyncio

from jarvis.core.bus import EventBus
from jarvis.core.config import VoiceBridgeConfig
from jarvis.core.events import (
    MessageSent,
    ResponseGenerated,
    TranscriptFinal,
    VoiceSessionEnded,
    VoiceTurnCompleted,
)
from jarvis.memory.wiki.voice_bridge import VoiceFactBridge

SUBSCRIBED_EVENTS = (
    TranscriptFinal,
    MessageSent,
    ResponseGenerated,
    VoiceTurnCompleted,
    VoiceSessionEnded,
)


def _bridge(bus: EventBus) -> VoiceFactBridge:
    return VoiceFactBridge(
        bus=bus, curator=None, config=VoiceBridgeConfig(), extractor=None
    )


def _attached(bus: EventBus) -> int:
    return sum(len(bus._subscribers.get(event, [])) for event in SUBSCRIBED_EVENTS)  # noqa: SLF001


def test_start_attaches_and_stop_detaches() -> None:
    bus = EventBus()
    bridge = _bridge(bus)

    bridge.start()
    assert _attached(bus) == len(SUBSCRIBED_EVENTS)

    bridge.stop()
    assert _attached(bus) == 0


def test_a_stopped_bridge_ignores_a_later_turn() -> None:
    """The leak's real symptom: work continued after the bridge was stopped."""
    bus = EventBus()
    bridge = _bridge(bus)
    bridge.start()
    bridge.stop()

    seen: list[str] = []

    async def _witness(event: VoiceTurnCompleted) -> None:
        seen.append(event.user_text)

    bus.subscribe(VoiceTurnCompleted, _witness)

    async def _drive() -> None:
        await bus.publish(
            VoiceTurnCompleted(user_text="after stop", jarvis_text="ok", tier="realtime")
        )

    asyncio.run(_drive())

    # The witness proves the event was really delivered, so an empty bridge
    # state cannot be mistaken for an event that never fired.
    assert seen == ["after stop"]
    assert _attached(bus) == 1  # only the witness


def test_stop_is_idempotent_and_restart_does_not_stack_handlers() -> None:
    bus = EventBus()
    bridge = _bridge(bus)

    bridge.start()
    bridge.stop()
    bridge.stop()
    assert _attached(bus) == 0

    bridge.start()
    assert _attached(bus) == len(SUBSCRIBED_EVENTS)
    bridge.stop()
    assert _attached(bus) == 0
