"""SymconActor: wraps JsonRpcClient + applies a hard timeout; FakeSymconActor."""

from __future__ import annotations

import asyncio

import pytest

from skillbook.symcon_bridge.actor import SymconActor
from skillbook.symcon_bridge.jsonrpc import JsonRpcClient
from tests.fakes.symcon import FakeSymconActor


async def test_symcon_actor_calls_underlying_rpc() -> None:
    async def fake_post(url: str, body: bytes, timeout_s: float) -> bytes:
        import json
        return json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"value": 42}}).encode()

    client = JsonRpcClient(url="http://ipsymcon/", http_post=fake_post)
    actor = SymconActor(name="dimmer", method="IPS_RequestAction", client=client, timeout_s=2.0)
    out = await actor.call({"id": 1, "value": 0.7})
    assert out == {"value": 42}


async def test_symcon_actor_raises_timeout_on_slow_rpc() -> None:
    async def slow_post(url: str, body: bytes, timeout_s: float) -> bytes:
        await asyncio.sleep(10)
        return b""

    client = JsonRpcClient(url="http://x/", http_post=slow_post)
    actor = SymconActor(name="slow", method="X", client=client, timeout_s=0.1)
    with pytest.raises(TimeoutError):
        await actor.call({})


async def test_fake_symcon_actor_fails_then_succeeds() -> None:
    actor = FakeSymconActor(name="flaky", failures_until_ok=1)
    with pytest.raises(TimeoutError):
        await actor.call({})
    out = await actor.call({"intensity": 0.5})
    assert out["ok"] is True
    assert actor.call_count == 2


async def test_fake_symcon_actor_always_succeeds_when_no_failures() -> None:
    actor = FakeSymconActor(name="reliable")
    out = await actor.call({"x": 1})
    assert out["ok"] is True
    assert actor.call_count == 1
