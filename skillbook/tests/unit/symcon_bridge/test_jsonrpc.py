"""JsonRpcClient: HTTP POST envelope + error handling + injectable transport."""

from __future__ import annotations

import json

import pytest

from skillbook.symcon_bridge.jsonrpc import JsonRpcClient, JsonRpcError


async def test_call_builds_jsonrpc_envelope() -> None:
    captured: list[bytes] = []

    async def fake_post(url: str, body: bytes, timeout_s: float) -> bytes:
        captured.append(body)
        return json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}).encode()

    client = JsonRpcClient(url="http://ipsymcon/api/", http_post=fake_post)
    result = await client.call("IPS_RequestAction", [123, True])

    assert result == {"ok": True}
    sent = json.loads(captured[0])
    assert sent["jsonrpc"] == "2.0"
    assert sent["method"] == "IPS_RequestAction"
    assert sent["params"] == [123, True]
    assert "id" in sent


async def test_call_raises_on_rpc_error_envelope() -> None:
    async def fake_post(url: str, body: bytes, timeout_s: float) -> bytes:
        return json.dumps({
            "jsonrpc": "2.0", "id": 1,
            "error": {"code": -32000, "message": "actor missing"},
        }).encode()

    client = JsonRpcClient(url="http://x/", http_post=fake_post)
    with pytest.raises(JsonRpcError) as exc:
        await client.call("DoThing", {})
    assert "actor missing" in str(exc.value)


async def test_call_raises_on_malformed_response() -> None:
    async def fake_post(url: str, body: bytes, timeout_s: float) -> bytes:
        return b"not json at all"

    client = JsonRpcClient(url="http://x/", http_post=fake_post)
    with pytest.raises(JsonRpcError):
        await client.call("X", {})
