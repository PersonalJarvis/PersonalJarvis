from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest

from jarvis.channels.base import ChannelMessage, ChannelSession
from jarvis.channels.chat_bridge import ChannelChatBridge
from jarvis.core.bus import EventBus
from jarvis.core.events import Event, MessageSent


class _QueueChannel:
    name = "telegram"

    def __init__(self) -> None:
        self.queue: asyncio.Queue[ChannelMessage] = asyncio.Queue()

    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def send_message(self, msg: ChannelMessage) -> None: ...
    async def broadcast_event(self, event: Event) -> None: ...

    async def messages(self) -> AsyncIterator[ChannelMessage]:
        while True:
            yield await self.queue.get()

    async def sessions(self) -> list[ChannelSession]:
        return []


class _Manager:
    def __init__(self, channel: _QueueChannel) -> None:
        self.channel = channel

    def started(self) -> list[str]:
        return ["telegram"]

    def get(self, name: str) -> _QueueChannel:
        assert name == "telegram"
        return self.channel


@pytest.mark.asyncio
async def test_bridge_publishes_channel_text_as_user_message() -> None:
    bus = EventBus()
    channel = _QueueChannel()
    bridge = ChannelChatBridge(bus=bus, manager=_Manager(channel))  # type: ignore[arg-type]
    seen: list[MessageSent] = []
    ready = asyncio.Event()

    async def _capture(event: MessageSent) -> None:
        seen.append(event)
        ready.set()

    bus.subscribe(MessageSent, _capture)
    bridge.start()
    trace_id = uuid4()
    await channel.queue.put(
        ChannelMessage(
            session_id=uuid4(),
            kind="text",
            content="Hallo",
            trace_id=trace_id,
            metadata={"telegram_chat_id": 12345},
        )
    )

    await asyncio.wait_for(ready.wait(), timeout=1.0)
    await bridge.stop()

    assert len(seen) == 1
    assert seen[0].trace_id == trace_id
    assert seen[0].thread_id == "telegram:12345"
    assert seen[0].role == "user"
    assert seen[0].text == "Hallo"
    assert seen[0].source_layer == "channel.telegram"


@pytest.mark.asyncio
async def test_bridge_ignores_empty_and_system_messages() -> None:
    bus = EventBus()
    channel = _QueueChannel()
    bridge = ChannelChatBridge(bus=bus, manager=_Manager(channel))  # type: ignore[arg-type]
    seen: list[MessageSent] = []

    async def _capture(event: MessageSent) -> None:
        seen.append(event)

    bus.subscribe(MessageSent, _capture)
    bridge.start()
    await channel.queue.put(
        ChannelMessage(session_id=uuid4(), kind="system", content="hidden")
    )
    await channel.queue.put(
        ChannelMessage(session_id=uuid4(), kind="text", content="   ")
    )
    await asyncio.sleep(0.05)
    await bridge.stop()

    assert seen == []
