"""MQTT subscriber for IP-Symcon (ADR-0005, amended by ADR-0010 / FORENSICS Q3).

Closes the FORENSICS Q3 gap that ``[mqtt]`` extra was declared in pyproject
but zero code in ``src/`` imported ``aiomqtt``. This module provides:

  - ``MqttSubscriber``: pure dispatch over an async message stream. Stream
    shape is duck-typed against ``aiomqtt.Message`` (objects with ``.topic``
    and ``.payload``). Subscriber-exception isolation per AP-18.
  - ``aiomqtt_message_stream``: an async generator that lazy-imports
    ``aiomqtt``, opens a real broker connection, subscribes to a topic
    filter, and yields incoming ``Message`` objects. Raises
    :class:`MissingAdapterError` when the ``[mqtt]`` extra is not installed.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from skillbook.errors import MissingAdapterError

MessageHandler = Callable[[bytes], Awaitable[None]]


def _topic_str(topic: Any) -> str:
    if isinstance(topic, str):
        return topic
    value = getattr(topic, "value", None)
    if isinstance(value, str):
        return value
    return str(topic)


@dataclass(slots=True)
class MqttSubscriber:
    """Dispatch incoming MQTT messages from a stream to per-topic handlers."""

    _handlers: dict[str, list[MessageHandler]] = field(default_factory=dict)

    def subscribe(self, topic: str, handler: MessageHandler) -> None:
        self._handlers.setdefault(topic, []).append(handler)

    async def consume(self, stream: AsyncIterator[Any]) -> None:
        async for message in stream:
            topic = _topic_str(getattr(message, "topic", ""))
            payload = getattr(message, "payload", b"")
            if not isinstance(payload, (bytes, bytearray)):
                payload = bytes(payload)
            handlers = list(self._handlers.get(topic, ()))
            for h in handlers:
                try:
                    await h(bytes(payload))
                except Exception:
                    continue


def aiomqtt_message_stream(
    *,
    host: str,
    port: int = 1883,
    topic_filter: str,
    keepalive: int = 60,
) -> AsyncIterator[Any]:
    """Lazy-import aiomqtt, connect to the broker, subscribe, yield messages."""
    try:
        import aiomqtt  # type: ignore[import-not-found]
    except ImportError as exc:
        raise MissingAdapterError(
            "aiomqtt",
            hint="Install the [mqtt] optional extra: pip install skillbook[mqtt].",
        ) from exc
    if aiomqtt is None:
        raise MissingAdapterError(
            "aiomqtt",
            hint="Install the [mqtt] optional extra: pip install skillbook[mqtt].",
        )

    async def _gen() -> AsyncIterator[Any]:
        async with aiomqtt.Client(hostname=host, port=port, keepalive=keepalive) as client:
            await client.subscribe(topic_filter)
            async for message in client.messages:
                yield message

    return _gen()
