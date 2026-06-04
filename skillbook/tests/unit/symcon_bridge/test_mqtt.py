"""MqttSubscriber: dispatch async messages from a stream to per-topic handlers."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import AsyncIterator

import pytest

from skillbook.errors import MissingAdapterError
from skillbook.symcon_bridge.mqtt import (
    MqttSubscriber,
    aiomqtt_message_stream,
)


@dataclass
class FakeMessage:
    topic: str
    payload: bytes


async def _stream(*messages: FakeMessage) -> AsyncIterator[FakeMessage]:
    for m in messages:
        yield m


async def test_subscriber_dispatches_to_topic_handler() -> None:
    sub = MqttSubscriber()
    received: list[bytes] = []

    async def handler(payload: bytes) -> None:
        received.append(payload)

    sub.subscribe("home/lamp/state", handler)
    await sub.consume(_stream(
        FakeMessage(topic="home/lamp/state", payload=b"on"),
        FakeMessage(topic="home/lamp/state", payload=b"off"),
    ))
    assert received == [b"on", b"off"]


async def test_subscriber_ignores_unsubscribed_topic() -> None:
    sub = MqttSubscriber()
    received: list[bytes] = []

    async def handler(payload: bytes) -> None:
        received.append(payload)

    sub.subscribe("home/lamp", handler)
    await sub.consume(_stream(
        FakeMessage(topic="home/door", payload=b"open"),
    ))
    assert received == []


async def test_multiple_handlers_for_same_topic_all_fire() -> None:
    sub = MqttSubscriber()
    a, b = [], []

    async def to_a(p: bytes) -> None:
        a.append(p)

    async def to_b(p: bytes) -> None:
        b.append(p)

    sub.subscribe("t", to_a)
    sub.subscribe("t", to_b)
    await sub.consume(_stream(FakeMessage(topic="t", payload=b"x")))
    assert a == [b"x"]
    assert b == [b"x"]


async def test_handler_exception_does_not_break_dispatch() -> None:
    sub = MqttSubscriber()
    good: list[bytes] = []

    async def bad(p: bytes) -> None:
        raise RuntimeError("subscriber crashed")

    async def good_handler(p: bytes) -> None:
        good.append(p)

    sub.subscribe("t", bad)
    sub.subscribe("t", good_handler)
    await sub.consume(_stream(FakeMessage(topic="t", payload=b"x")))
    assert good == [b"x"]


async def test_subscriber_supports_topic_with_attribute_value() -> None:
    class TopicObj:
        def __init__(self, v: str) -> None:
            self.value = v

        def __str__(self) -> str:
            return self.value

    sub = MqttSubscriber()
    seen: list[bytes] = []

    async def h(p: bytes) -> None:
        seen.append(p)

    sub.subscribe("home/lamp", h)
    msg = FakeMessage(topic=TopicObj("home/lamp"), payload=b"on")
    await sub.consume(_stream(msg))
    assert seen == [b"on"]


def test_aiomqtt_message_stream_raises_missing_adapter_without_sdk(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "aiomqtt", None)
    with pytest.raises(MissingAdapterError) as exc:
        gen = aiomqtt_message_stream(host="localhost", port=1883, topic_filter="#")
        assert gen is not None
    assert "aiomqtt" in str(exc.value)
