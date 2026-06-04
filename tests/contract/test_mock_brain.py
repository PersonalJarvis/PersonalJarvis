"""Contract-Test: MockBrain reagiert auf MessageSent und triggert State-Flow.

E2E-mini: User-Message → Bus → Mock-Brain → Response + State-Transitions.
Verifiziert, dass das Event-Wiring in `DesktopApp._run_backend` stehen kann,
ohne dass wir den echten WebServer starten müssen.
"""
from __future__ import annotations

import asyncio

import pytest

from jarvis.brain.mock import MockBrain
from jarvis.core.bus import EventBus
from jarvis.core.events import (
    Event,
    MessageSent,
    ResponseGenerated,
    SystemStateChanged,
)
from jarvis.state.chat_store import ChatStore
from jarvis.state.supervisor import Supervisor


@pytest.mark.asyncio
async def test_mock_brain_produces_assistant_message_and_state_sequence() -> None:
    bus = EventBus()
    supervisor = Supervisor(bus=bus)
    store = ChatStore(bus=bus)
    brain = MockBrain(bus=bus, supervisor=supervisor)

    collected: list[Event] = []

    async def _collect(evt: Event) -> None:
        collected.append(evt)

    bus.subscribe_all(_collect)

    await brain.respond(thread_id="t1", text="Hallo Jarvis", store=store)

    # Es muss mindestens ein SystemStateChanged-Paar geben (THINKING, SPEAKING, IDLE)
    states = [e.new_state for e in collected if isinstance(e, SystemStateChanged)]
    assert "THINKING" in states
    assert "SPEAKING" in states
    assert states[-1] == "IDLE"

    # Eine ResponseGenerated muss drin sein
    assert any(isinstance(e, ResponseGenerated) for e in collected)

    # Store hat genau einen User-Turn + einen Assistant-Turn
    thread = store.get_thread("t1")
    assert thread is not None
    roles = [m["role"] for m in thread["messages"]]
    assert roles == ["user", "assistant"]


@pytest.mark.asyncio
async def test_message_sent_event_triggers_brain_via_subscribe() -> None:
    """Simulation des DesktopApp-Wiring: bus.subscribe(MessageSent, ...)."""
    bus = EventBus()
    supervisor = Supervisor(bus=bus)
    store = ChatStore(bus=bus)
    brain = MockBrain(bus=bus, supervisor=supervisor)

    async def _on_msg(evt: MessageSent) -> None:
        if evt.role != "user":
            return
        # Skip echoes aus dem ChatStore selbst — sonst Loop.
        if evt.source_layer in ("chat", "brain:mock"):
            return
        await brain.respond(thread_id=evt.thread_id or "default", text=evt.text, store=store)

    bus.subscribe(MessageSent, _on_msg)

    # User-Message reinfahren — das ist was routes_ws macht
    await bus.publish(
        MessageSent(source_layer="ui.web.ws", thread_id="t1", role="user", text="echo hallo")
    )

    # Kurze Pause damit die Event-Chain durchrieselt
    await asyncio.sleep(0.6)

    thread = store.get_thread("t1")
    assert thread is not None
    assert len(thread["messages"]) == 2
    assert thread["messages"][1]["role"] == "assistant"
    assert thread["messages"][1]["text"] == "hallo"
