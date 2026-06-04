"""Bridge ChannelAdapter inboxes into the normal Jarvis chat event path."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from jarvis.channels.base import ChannelMessage
from jarvis.channels.manager import ChannelManager
from jarvis.core.bus import EventBus
from jarvis.core.events import MessageSent
from jarvis.core.protocols import ChannelAdapter

log = logging.getLogger(__name__)


class ChannelChatBridge:
    """Consumes channel inboxes and publishes ``MessageSent(role="user")``.

    The web UI already enters the chat path through ``WebServer._route_incoming``.
    External channels such as Telegram only expose an async ``messages()``
    iterator, so they need this small runtime bridge. The original
    ``ChannelMessage.trace_id`` is preserved; response channels use that id to
    route the assistant reply back to the originating chat.
    """

    def __init__(self, *, bus: EventBus, manager: ChannelManager) -> None:
        self._bus = bus
        self._manager = manager
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def start(self) -> None:
        for name in self._manager.started():
            if name in self._tasks:
                continue
            try:
                channel = self._manager.get(name)
            except Exception as exc:  # noqa: BLE001
                log.warning("ChannelChatBridge konnte '%s' nicht holen: %s", name, exc)
                continue
            self._tasks[name] = asyncio.create_task(
                self._consume(name, channel),
                name=f"channel-chat-bridge:{name}",
            )
        log.info("ChannelChatBridge gestartet fuer Channels: %s", sorted(self._tasks))

    async def stop(self) -> None:
        if not self._tasks:
            return
        tasks = list(self._tasks.values())
        self._tasks.clear()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        log.info("ChannelChatBridge gestoppt")

    async def _consume(self, name: str, channel: ChannelAdapter) -> None:
        try:
            async for msg in channel.messages():
                await self._handle_message(name, msg)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("ChannelChatBridge consumer fuer '%s' beendet: %s", name, exc)

    async def _handle_message(self, name: str, msg: ChannelMessage) -> None:
        if msg.kind not in {"text", "voice"}:
            return
        text = (msg.content or "").strip()
        if not text:
            return
        await self._bus.publish(
            MessageSent(
                trace_id=msg.trace_id,
                thread_id=_thread_id_for(name, msg),
                role="user",
                text=text,
                source_layer=f"channel.{name}",
            )
        )


def _thread_id_for(name: str, msg: ChannelMessage) -> str:
    chat_id = _metadata_value(msg.metadata, "telegram_chat_id")
    if chat_id:
        return f"telegram:{chat_id}"
    channel_session = _metadata_value(msg.metadata, "channel_session_id")
    if channel_session:
        return f"{name}:{channel_session}"
    return f"{name}:{msg.session_id}"


def _metadata_value(metadata: dict[str, Any], key: str) -> str:
    value = metadata.get(key)
    if value is None:
        return ""
    text = str(value).strip()
    return text
