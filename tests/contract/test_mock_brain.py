"""Contract test: MockBrain reacts to MessageSent and triggers the state flow.

E2E-mini: user message → bus → mock brain → response + state transitions.
Verifies that the event wiring can stand in for `DesktopApp._run_backend`
without us having to start the real WebServer.
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

    # There must be at least one SystemStateChanged pair (THINKING, SPEAKING, IDLE)
    states = [e.new_state for e in collected if isinstance(e, SystemStateChanged)]
    assert "THINKING" in states
    assert "SPEAKING" in states
    assert states[-1] == "IDLE"

    # A ResponseGenerated must be in there
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
        # Skip echoes from the ChatStore itself — otherwise a loop.
        if evt.source_layer in ("chat", "brain:mock"):
            return
        await brain.respond(thread_id=evt.thread_id or "default", text=evt.text, store=store)

    bus.subscribe(MessageSent, _on_msg)

    # Feed in a user message — that's what routes_ws does
    await bus.publish(
        MessageSent(source_layer="ui.web.ws", thread_id="t1", role="user", text="echo hallo")
    )

    # Brief pause so the event chain trickles through
    await asyncio.sleep(0.6)

    thread = store.get_thread("t1")
    assert thread is not None
    assert len(thread["messages"]) == 2
    assert thread["messages"][1]["role"] == "assistant"
    assert thread["messages"][1]["text"] == "hallo"
