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


def test_tool_contract_shape():
    tool = GmailRestTool()
    assert tool.name == "gmail"
    assert tool.risk_tier == "ask"  # send is consequential
    assert "schema" in dir(tool)
