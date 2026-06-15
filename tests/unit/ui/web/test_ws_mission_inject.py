"""A `mission.inject` WS command publishes a MessageSent that the brain answers."""
from __future__ import annotations

import asyncio

from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.core.events import MessageSent
from jarvis.ui.web.schema import WSCommand
from jarvis.ui.web.server import WebServer


async def test_mission_inject_publishes_message_sent() -> None:
    bus = EventBus()
    seen: list[MessageSent] = []
    bus.subscribe(MessageSent, lambda e: seen.append(e))  # type: ignore[arg-type]

    srv = WebServer(JarvisConfig(), bus=bus)
    cmd = WSCommand(
        type="command",
        action="mission.inject",
        payload={
            "slug": "20260615__recherchiere__abc",
            "utterance": "recherchiere AI-News",
            "status": "success",
            "summary": "Three reports found.",
            "thread_id": "thread-7",
        },
    )
    await srv._handle_command("sess-1", cmd, asyncio.Lock())
    await asyncio.sleep(0)  # let fire-and-forget dispatch settle

    assert len(seen) == 1
    msg = seen[0]
    assert msg.role == "user"
    assert msg.thread_id == "thread-7"
    assert msg.source_layer == "ui.web.ws.mission_inject"
    assert "recherchiere AI-News" in msg.text


async def test_mission_inject_empty_payload_publishes_nothing() -> None:
    bus = EventBus()
    seen: list[MessageSent] = []
    bus.subscribe(MessageSent, lambda e: seen.append(e))  # type: ignore[arg-type]

    srv = WebServer(JarvisConfig(), bus=bus)
    cmd = WSCommand(type="command", action="mission.inject", payload={})
    await srv._handle_command("sess-1", cmd, asyncio.Lock())
    await asyncio.sleep(0)

    assert seen == []
