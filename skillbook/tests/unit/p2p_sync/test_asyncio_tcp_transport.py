"""AsyncioTcpTransport: real TCP roundtrip on 127.0.0.1 between two endpoints."""

from __future__ import annotations

import asyncio
import socket

import pytest

from skillbook.p2p_sync.tcp_transport import AsyncioTcpTransport


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
async def tcp_pair():
    port_a = _free_port()
    port_b = _free_port()
    a = AsyncioTcpTransport(
        listen_host="127.0.0.1", listen_port=port_a,
        peer_addrs=[("127.0.0.1", port_b)],
    )
    b = AsyncioTcpTransport(
        listen_host="127.0.0.1", listen_port=port_b,
        peer_addrs=[("127.0.0.1", port_a)],
    )
    await a.start()
    await b.start()
    yield a, b
    await a.stop()
    await b.stop()


async def test_gossip_from_a_delivers_over_tcp_to_b(tcp_pair) -> None:
    a, b = tcp_pair
    received: list[bytes] = []
    delivered = asyncio.Event()

    async def handler(payload: bytes) -> None:
        received.append(payload)
        delivered.set()

    b.subscribe(handler)
    await a.gossip(b"hello over tcp")
    await asyncio.wait_for(delivered.wait(), timeout=2.0)
    assert received == [b"hello over tcp"]


async def test_gossip_is_bidirectional_over_tcp(tcp_pair) -> None:
    a, b = tcp_pair
    a_got, b_got = [], []
    a_done, b_done = asyncio.Event(), asyncio.Event()

    async def to_a(p: bytes) -> None:
        a_got.append(p)
        a_done.set()

    async def to_b(p: bytes) -> None:
        b_got.append(p)
        b_done.set()

    a.subscribe(to_a)
    b.subscribe(to_b)
    await a.gossip(b"a->b")
    await b.gossip(b"b->a")
    await asyncio.wait_for(a_done.wait(), timeout=2.0)
    await asyncio.wait_for(b_done.wait(), timeout=2.0)
    assert a_got == [b"b->a"]
    assert b_got == [b"a->b"]


async def test_gossip_to_offline_peer_does_not_raise() -> None:
    dead_port = _free_port()
    a = AsyncioTcpTransport(
        listen_host="127.0.0.1", listen_port=_free_port(),
        peer_addrs=[("127.0.0.1", dead_port)],
    )
    await a.start()
    try:
        await a.gossip(b"nobody listens")
    finally:
        await a.stop()


async def test_subscriber_exception_isolated(tcp_pair) -> None:
    a, b = tcp_pair
    good: list[bytes] = []
    delivered = asyncio.Event()

    async def bad_handler(p: bytes) -> None:
        raise RuntimeError("subscriber crashed")

    async def good_handler(p: bytes) -> None:
        good.append(p)
        delivered.set()

    b.subscribe(bad_handler)
    b.subscribe(good_handler)
    await a.gossip(b"payload")
    await asyncio.wait_for(delivered.wait(), timeout=2.0)
    assert good == [b"payload"]


async def test_large_payload_with_length_prefix(tcp_pair) -> None:
    a, b = tcp_pair
    received: list[bytes] = []
    delivered = asyncio.Event()

    async def handler(p: bytes) -> None:
        received.append(p)
        delivered.set()

    b.subscribe(handler)
    payload = b"x" * 65000
    await a.gossip(payload)
    await asyncio.wait_for(delivered.wait(), timeout=2.0)
    assert received == [payload]
    assert len(received[0]) == 65000
