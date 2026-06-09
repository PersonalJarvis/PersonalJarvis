"""Gmail REST tool: reads the marketplace keyring token and calls the Gmail
REST API directly (Node-free, stays under the marketplace token model)."""

import base64

import httpx
import pytest

from jarvis.plugins.tool.gmail_rest import GmailRestTool


@pytest.mark.asyncio
async def test_list_messages_uses_bearer_from_provider():
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers["authorization"] == "Bearer at_123"
        assert "/messages" in str(req.url)
        return httpx.Response(200, json={"messages": [{"id": "m1"}]})

    tool = GmailRestTool(
        access_token_provider=lambda: "at_123",
        transport=httpx.MockTransport(handler),
    )
    out = await tool.list_messages(max_results=1)
    assert out["messages"][0]["id"] == "m1"


@pytest.mark.asyncio
async def test_send_message_posts_base64_raw():
    captured = {}

    def handler(req: httpx.Request) -> httpx.Response:
        import json

        captured["url"] = str(req.url)
        captured["body"] = json.loads(req.content)
        return httpx.Response(200, json={"id": "sent1"})

    tool = GmailRestTool(
        access_token_provider=lambda: "at_123",
        transport=httpx.MockTransport(handler),
    )
    out = await tool.send_message(to="a@b.com", subject="Hi", body="Hello")
    assert out["id"] == "sent1"
    assert "/messages/send" in captured["url"]
    decoded = base64.urlsafe_b64decode(captured["body"]["raw"]).decode()
    assert "To: a@b.com" in decoded and "Hello" in decoded


@pytest.mark.asyncio
async def test_execute_returns_error_when_not_connected():
    tool = GmailRestTool(access_token_provider=lambda: None)
    result = await tool.execute({"action": "list_messages"}, ctx=None)
    assert result.success is False
    assert "connect" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_list_messages_refreshes_on_401_then_retries():
    # An expired access token returns 401. The tool must refresh once and retry
    # the call — self-healing instead of surfacing "Freigabe abgelaufen"
    # (live 2026-06-07 Gmail bug).
    calls = {"http": 0, "refresh": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["http"] += 1
        if calls["http"] == 1:
            return httpx.Response(401, json={"error": {"code": 401}})
        return httpx.Response(200, json={"messages": [{"id": "m9"}]})

    async def refresher() -> bool:
        calls["refresh"] += 1
        return True

    tool = GmailRestTool(
        access_token_provider=lambda: "at_dead",
        transport=httpx.MockTransport(handler),
        token_refresher=refresher,
    )
    out = await tool.list_messages(max_results=1)
    assert out["messages"][0]["id"] == "m9"
    assert calls["refresh"] == 1
    assert calls["http"] == 2  # retried exactly once after a successful refresh


@pytest.mark.asyncio
async def test_returns_reconnect_error_when_refresh_fails():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"code": 401}})

    async def refresher() -> bool:
        return False  # un-healable (e.g. invalid_client / revoked)

    tool = GmailRestTool(
        access_token_provider=lambda: "at_dead",
        transport=httpx.MockTransport(handler),
        token_refresher=refresher,
    )
    out = await tool.list_messages()
    assert "error" in out
    assert "reconnect" in out["error"].lower()


@pytest.mark.asyncio
async def test_send_message_refreshes_on_401_then_retries():
    calls = {"http": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["http"] += 1
        if calls["http"] == 1:
            return httpx.Response(401, json={"error": {"code": 401}})
        return httpx.Response(200, json={"id": "sent9"})

    async def refresher() -> bool:
        return True

    tool = GmailRestTool(
        access_token_provider=lambda: "at_dead",
        transport=httpx.MockTransport(handler),
        token_refresher=refresher,
    )
    out = await tool.send_message(to="a@b.com", subject="x", body="y")
    assert out["id"] == "sent9"
    assert calls["http"] == 2


@pytest.mark.asyncio
async def test_non_401_error_is_not_retried():
    # A 500 is not an auth problem — do not refresh, do not retry; let it surface.
    calls = {"http": 0, "refresh": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["http"] += 1
        return httpx.Response(500, json={"error": "boom"})

    async def refresher() -> bool:
        calls["refresh"] += 1
        return True

    tool = GmailRestTool(
        access_token_provider=lambda: "at_123",
        transport=httpx.MockTransport(handler),
        token_refresher=refresher,
    )
    result = await tool.execute({"action": "list_messages"}, ctx=None)
    assert result.success is False
    assert calls["refresh"] == 0
    assert calls["http"] == 1


def test_tool_contract_shape():
    tool = GmailRestTool()
    assert tool.name == "gmail"
    assert tool.risk_tier == "ask"  # send is consequential
    assert "schema" in dir(tool)
