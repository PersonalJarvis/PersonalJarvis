"""InProcessTransport: pair() routes gossip to peer's handler synchronously."""

from __future__ import annotations

import asyncio

from tests.fakes.transport import InProcessTransport


async def test_pair_returns_two_distinct_endpoints() -> None:
    a, b = InProcessTransport.pair()
    assert a is not b


async def test_gossip_on_one_endpoint_delivers_to_peers_handler() -> None:
    a, b = InProcessTransport.pair()
    received: list[bytes] = []

    async def handler(payload: bytes) -> None:
        received.append(payload)

    b.subscribe(handler)
    await a.gossip(b"hello")
    assert received == [b"hello"]


async def test_gossip_is_bidirectional() -> None:
    a, b = InProcessTransport.pair()
    a_received: list[bytes] = []
    b_received: list[bytes] = []

    async def to_a(p: bytes) -> None:
        a_received.append(p)

    async def to_b(p: bytes) -> None:
        b_received.append(p)

    a.subscribe(to_a)
    b.subscribe(to_b)
    await a.gossip(b"a->b")
    await b.gossip(b"b->a")
    assert a_received == [b"b->a"]
    assert b_received == [b"a->b"]


async def test_handler_exception_does_not_poison_other_handlers() -> None:
    a, b = InProcessTransport.pair()
    good_received: list[bytes] = []

    async def bad_handler(p: bytes) -> None:
        raise RuntimeError("subscriber crashed")

    async def good_handler(p: bytes) -> None:
        good_received.append(p)

    b.subscribe(bad_handler)
    b.subscribe(good_handler)
    await a.gossip(b"payload")
    assert good_received == [b"payload"]
