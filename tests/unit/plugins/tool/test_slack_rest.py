"""Slack REST tool: calls the Slack Web API with the marketplace user token.

Native instead of Slack's hosted MCP server, which admits only
Marketplace-listed or internal apps. All HTTP goes through httpx.MockTransport.
"""

from __future__ import annotations

import json

import httpx
import pytest

from jarvis.plugins.tool.slack_rest import SlackRestTool

_C = "C0123ABCD"


def _ok(**body):
    return httpx.Response(200, json={"ok": True, **body})


def _fail(code: str, **extra):
    return httpx.Response(200, json={"ok": False, "error": code, **extra})


def _tool(handler, *, access: str | None = "xoxp-at", refresher=None, sleeps=None):
    tokens = {"value": access}

    async def _sleep(seconds: float) -> None:
        if sleeps is not None:
            sleeps.append(seconds)

    tool = SlackRestTool(
        access_token_provider=lambda: tokens["value"],
        transport=httpx.MockTransport(handler),
        token_refresher=refresher,
        sleep=_sleep,
    )
    return tool, tokens


def _method(req: httpx.Request) -> str:
    return req.url.path.rsplit("/", 1)[-1]


@pytest.mark.asyncio
async def test_search_messages_uses_bearer_and_slims_matches():
    seen = []

    def handler(req):
        seen.append(req)
        assert _method(req) == "search.messages"
        return _ok(
            messages={
                "total": 1,
                "paging": {"page": 1, "pages": 1},
                "matches": [
                    {
                        "channel": {"id": _C, "name": "general"},
                        "user": "U0AAAAAAA",
                        "username": "anna",
                        "text": "Release is on Friday",
                        "ts": "1700000000.000100",
                        "permalink": "https://example.slack.com/archives/C0123ABCD/p1",
                        "blocks": [{"huge": "payload"}],
                    }
                ],
            }
        )

    tool, _ = _tool(handler)
    result = await tool.execute({"action": "search_messages", "query": "release"}, None)
    assert result.success, result.error
    assert seen[0].headers["authorization"] == "Bearer xoxp-at"
    assert seen[0].url.params["query"] == "release"
    assert seen[0].url.params["sort"] == "timestamp"
    message = result.output["messages"][0]
    assert message == {
        "channel_id": _C,
        "channel": "general",
        "user": "U0AAAAAAA",
        "user_name": "anna",
        "text": "Release is on Friday",
        "ts": "1700000000.000100",
        "permalink": "https://example.slack.com/archives/C0123ABCD/p1",
    }
    assert result.output["total"] == 1


@pytest.mark.asyncio
async def test_list_conversations_follows_cursor_and_names_dms():
    pages = []

    def handler(req):
        method = _method(req)
        if method == "conversations.list":
            pages.append(dict(req.url.params))
            if "cursor" not in req.url.params:
                return _ok(
                    channels=[{"id": _C, "name": "general", "is_channel": True, "is_member": True}],
                    response_metadata={"next_cursor": "page2"},
                )
            return _ok(
                channels=[{"id": "D0123ABCD", "is_im": True, "user": "U0BBBBBBB"}],
                response_metadata={"next_cursor": ""},
            )
        if method == "users.info":
            assert req.url.params["user"] == "U0BBBBBBB"
            return _ok(user={"id": "U0BBBBBBB", "profile": {"display_name": "Ben"}})
        raise AssertionError(method)

    tool, _ = _tool(handler)
    out = await tool.list_conversations()
    assert pages[0]["types"] == "public_channel,private_channel,mpim,im"
    assert pages[0]["exclude_archived"] == "true"
    assert pages[1]["cursor"] == "page2"
    assert out["count"] == 2
    assert out["conversations"][0]["kind"] == "channel"
    assert out["conversations"][1] == {
        "id": "D0123ABCD",
        "name": "Ben",
        "kind": "dm",
        "user": "U0BBBBBBB",
    }
    assert "next_cursor" not in out


@pytest.mark.asyncio
async def test_list_conversations_returns_cursor_when_limit_reached():
    def handler(req):
        return _ok(
            channels=[{"id": _C, "name": "general"}],
            response_metadata={"next_cursor": "more"},
        )

    tool, _ = _tool(handler)
    out = await tool.list_conversations(limit=1, types="public_channel")
    assert out["count"] == 1
    assert out["next_cursor"] == "more"


@pytest.mark.asyncio
async def test_read_history_resolves_channel_name_and_user_names():
    calls = []

    def handler(req):
        method = _method(req)
        calls.append(method)
        if method == "conversations.list":
            assert req.url.params["types"] == "public_channel,private_channel,mpim"
            return _ok(channels=[{"id": "C0OTHER00", "name": "random"}, {"id": _C, "name": "team"}])
        if method == "conversations.history":
            assert req.url.params["channel"] == _C
            return _ok(
                messages=[
                    {
                        "ts": "2",
                        "user": "U0AAAAAAA",
                        "text": "hi",
                        "reply_count": 3,
                        "thread_ts": "2",
                    }
                ],
                has_more=True,
                response_metadata={"next_cursor": "older"},
            )
        if method == "users.info":
            return _ok(user={"id": "U0AAAAAAA", "real_name": "Anna Example"})
        raise AssertionError(method)

    tool, _ = _tool(handler)
    result = await tool.execute({"action": "read_history", "channel": "#team"}, None)
    assert result.success, result.error
    out = result.output
    assert out["channel_id"] == _C
    assert out["messages"][0]["user_name"] == "Anna Example"
    assert out["messages"][0]["reply_count"] == 3
    assert out["next_cursor"] == "older"
    assert calls == ["conversations.list", "conversations.history", "users.info"]


@pytest.mark.asyncio
async def test_read_thread_passes_ts():
    def handler(req):
        assert _method(req) == "conversations.replies"
        assert req.url.params["ts"] == "1700.1"
        return _ok(messages=[{"ts": "1700.1", "bot_id": "B1", "text": "parent"}], has_more=False)

    tool, _ = _tool(handler)
    out = await tool.read_thread(channel=_C, thread_ts="1700.1")
    assert out["messages"][0]["text"] == "parent"
    assert out["order"] == "oldest first"


@pytest.mark.asyncio
async def test_dm_resolution_uses_existing_im_only():
    def handler(req):
        method = _method(req)
        if method == "users.list":
            return _ok(
                members=[
                    {"id": "U0AAAAAAA", "name": "anna", "real_name": "Anna Example"},
                    {"id": "U0CCCCCCC", "name": "carl", "real_name": "Carl"},
                ]
            )
        if method == "conversations.list":
            assert req.url.params["types"] == "im"
            return _ok(channels=[{"id": "D0123ABCD", "is_im": True, "user": "U0AAAAAAA"}])
        if method == "conversations.history":
            assert req.url.params["channel"] == "D0123ABCD"
            return _ok(messages=[], has_more=False)
        if method == "users.info":
            raise AssertionError("directory already named the user")
        raise AssertionError(method)

    tool, _ = _tool(handler)
    out = await tool.read_history(channel="@anna")
    assert out["channel_id"] == "D0123ABCD"


@pytest.mark.asyncio
async def test_dm_without_existing_conversation_is_reported_not_opened():
    def handler(req):
        method = _method(req)
        if method == "users.list":
            return _ok(members=[{"id": "U0AAAAAAA", "name": "anna"}])
        if method == "conversations.list":
            return _ok(channels=[])
        raise AssertionError(f"must not call {method}")

    tool, _ = _tool(handler)
    result = await tool.execute(
        {"action": "post_message", "channel": "@anna", "text": "hi"}, None
    )
    assert not result.success
    assert "no direct message" in result.error


@pytest.mark.asyncio
async def test_ambiguous_person_returns_candidates():
    def handler(req):
        return _ok(
            members=[
                {"id": "U0AAAAAAA", "name": "anna.a", "real_name": "Anna A"},
                {"id": "U0BBBBBBB", "name": "anna.b", "real_name": "Anna B"},
            ]
        )

    tool, _ = _tool(handler)
    result = await tool.execute({"action": "read_history", "channel": "@anna"}, None)
    assert not result.success
    assert "Several people" in result.error
    assert {c["id"] for c in result.output["candidates"]} == {"U0AAAAAAA", "U0BBBBBBB"}


@pytest.mark.asyncio
async def test_unknown_channel_name_is_never_guessed():
    def handler(req):
        return _ok(channels=[{"id": _C, "name": "general"}])

    tool, _ = _tool(handler)
    result = await tool.execute({"action": "read_history", "channel": "#nope"}, None)
    assert not result.success
    assert "#nope" in result.error


@pytest.mark.asyncio
async def test_post_message_sends_json_with_thread():
    seen = []

    def handler(req):
        seen.append(req)
        assert _method(req) == "chat.postMessage"
        return _ok(channel=_C, ts="1700.2")

    tool, _ = _tool(handler)
    result = await tool.execute(
        {"action": "post_message", "channel": _C, "text": "On my way", "thread_ts": "1700.1"},
        None,
    )
    assert result.success, result.error
    assert seen[0].method == "POST"
    assert seen[0].headers["content-type"].startswith("application/json")
    assert json.loads(seen[0].content) == {
        "channel": _C,
        "text": "On my way",
        "thread_ts": "1700.1",
    }
    assert result.output == {
        "posted": True,
        "channel_id": _C,
        "ts": "1700.2",
        "thread_ts": "1700.1",
    }


@pytest.mark.asyncio
async def test_post_message_rejects_empty_text_without_calling_slack():
    def handler(req):
        raise AssertionError("no call expected")

    tool, _ = _tool(handler)
    result = await tool.execute({"action": "post_message", "channel": _C, "text": " "}, None)
    assert not result.success
    assert "empty" in result.error


@pytest.mark.asyncio
async def test_token_expired_refreshes_once_and_retries():
    attempts = []
    refreshes = []

    def handler(req):
        attempts.append(req.headers["authorization"])
        if req.headers["authorization"] == "Bearer xoxp-old":
            return _fail("token_expired")
        return _ok(user={"id": "U0AAAAAAA", "name": "anna"})

    async def refresher():
        refreshes.append(1)
        tokens["value"] = "xoxp-new"
        return True

    tool, tokens = _tool(handler, access="xoxp-old", refresher=refresher)
    out = await tool.get_user(user="U0AAAAAAA")
    assert out["name"] == "anna"
    assert attempts == ["Bearer xoxp-old", "Bearer xoxp-new"]
    assert refreshes == [1]


@pytest.mark.asyncio
async def test_failed_refresh_asks_for_reconnect():
    def handler(req):
        return _fail("token_expired")

    async def refresher():
        return False

    tool, _ = _tool(handler, refresher=refresher)
    result = await tool.execute({"action": "get_user", "user": "U0AAAAAAA"}, None)
    assert not result.success
    assert "reconnect Slack" in result.error


@pytest.mark.asyncio
async def test_revoked_token_does_not_try_refresh():
    async def refresher():
        raise AssertionError("revoked tokens cannot be refreshed")

    tool, _ = _tool(lambda req: _fail("token_revoked"), refresher=refresher)
    result = await tool.execute({"action": "search_messages", "query": "x"}, None)
    assert not result.success
    assert "reconnect Slack" in result.error


@pytest.mark.asyncio
async def test_provider_error_text_never_reaches_the_result():
    leaked = "xoxp-LEAKED-value"

    def handler(req):
        return _fail(leaked, detail=leaked)

    tool, _ = _tool(handler)
    result = await tool.execute({"action": "search_messages", "query": "x"}, None)
    assert not result.success
    assert leaked not in result.error
    assert result.error == "Slack could not complete the request. Try again in a moment."


@pytest.mark.asyncio
async def test_missing_scope_explains_reconnect():
    tool, _ = _tool(lambda req: _fail("missing_scope", needed="search:read"))
    result = await tool.execute({"action": "search_messages", "query": "x"}, None)
    assert not result.success
    assert "permission" in result.error


@pytest.mark.asyncio
async def test_short_rate_limit_is_absorbed_once():
    sleeps = []
    calls = []

    def handler(req):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "1"})
        return _ok(messages={"matches": [], "total": 0})

    tool, _ = _tool(handler, sleeps=sleeps)
    out = await tool.search_messages(query="x")
    assert out["messages"] == []
    assert sleeps == [1.0]
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_long_rate_limit_is_reported_with_wait():
    sleeps = []
    tool, _ = _tool(
        lambda req: httpx.Response(429, headers={"Retry-After": "30"}), sleeps=sleeps
    )
    result = await tool.execute({"action": "post_message", "channel": _C, "text": "hi"}, None)
    assert not result.success
    assert "30 seconds" in result.error
    assert sleeps == []


@pytest.mark.asyncio
async def test_not_connected():
    tool, _ = _tool(lambda req: _ok(), access=None)
    result = await tool.execute({"action": "list_conversations"}, None)
    assert not result.success
    assert "not connected" in result.error


@pytest.mark.asyncio
async def test_server_error_is_explained_without_body():
    tool, _ = _tool(lambda req: httpx.Response(503, text="internal xoxp-secret"))
    result = await tool.execute({"action": "find_users", "query": "anna"}, None)
    assert not result.success
    assert "xoxp" not in result.error
    assert "trouble" in result.error


def test_risk_tiers_reads_safe_post_asks():
    tool = SlackRestTool(access_token_provider=lambda: "t")
    for action in (
        "search_messages",
        "list_conversations",
        "read_history",
        "read_thread",
        "get_user",
        "find_users",
    ):
        assert tool.risk_tier_for_args({"action": action}) == "safe"
    assert tool.risk_tier_for_args({"action": "post_message"}) == "ask"
    assert tool.risk_tier_for_args({"action": "something_else"}) == "ask"
    assert tool.risk_tier == "ask"


def test_entry_point_and_catalog_bind_the_tool():
    import tomllib
    from pathlib import Path

    from jarvis.marketplace.catalog import PluginCatalog
    from jarvis.marketplace.catalog_data import _PACKAGE_SEED_PATH

    root = Path(__file__).resolve().parents[4]
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    tools = pyproject["project"]["entry-points"]["jarvis.tool"]
    assert tools["slack"] == "jarvis.plugins.tool.slack_rest:SlackRestTool"

    catalog = PluginCatalog.model_validate(
        json.loads(_PACKAGE_SEED_PATH.read_text(encoding="utf-8"))
    )
    slack = catalog.by_id("slack")
    assert slack is not None
    assert slack.native_tool == "slack"
    assert slack.mcp_server is None
    assert slack.auth.mode == "oauth_pkce_loopback"
    assert slack.auth.callback_port == 3118
    assert slack.auth.user_scopes_only is True
    assert slack.auth.scopes == [
        "chat:write",
        "users:read",
        "search:read",
        "channels:history",
        "groups:history",
        "im:history",
        "mpim:history",
        "channels:read",
        "groups:read",
        "im:read",
        "mpim:read",
    ]
