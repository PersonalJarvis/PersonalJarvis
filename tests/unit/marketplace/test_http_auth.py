"""Remote plugin sessions must survive token rotation without browser consent."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from jarvis.marketplace.catalog_data import load_catalog
from jarvis.marketplace.http_auth import PluginHttpAuth
from jarvis.marketplace.token_store import InMemoryBackend, Tokens, TokenStore
from tests.fakes.fake_plugin_http import PluginMcpTransport, RotatingPluginHandler

URL = "https://plugin.example/mcp"


def _setup(seconds: int = 3600):
    store = TokenStore(InMemoryBackend())
    store.save(
        "notion",
        Tokens(
            access="old-access",
            refresh="old-refresh",
            expires_at=datetime.now(UTC) + timedelta(seconds=seconds),
        ),
    )
    handler = RotatingPluginHandler()
    auth = PluginHttpAuth("notion", URL, store, lambda _: handler)
    return store, handler, auth


@pytest.mark.asyncio
async def test_each_request_reads_rotations_from_other_instances():
    store, handler, auth = _setup()
    seen = []

    def respond(request):
        seen.append(request.headers["Authorization"])
        return httpx.Response(200)

    async with httpx.AsyncClient(auth=auth, transport=httpx.MockTransport(respond)) as client:
        await client.post(URL)
        store.save("notion", Tokens(access="another-instance", refresh="rotated"))
        await client.post(URL)
    assert seen == ["Bearer old-access", "Bearer another-instance"]
    assert handler.calls == []


@pytest.mark.asyncio
async def test_expired_access_is_refreshed_before_the_first_request():
    store, handler, auth = _setup(-1)
    seen = []

    def respond(request):
        seen.append(request.headers["Authorization"])
        return httpx.Response(200)

    async with httpx.AsyncClient(auth=auth, transport=httpx.MockTransport(respond)) as client:
        await client.post(URL)
    assert seen == ["Bearer fresh-access"]
    assert handler.calls == ["old-refresh"]
    assert store.load("notion").refresh == "fresh-refresh"


@pytest.mark.asyncio
async def test_401_refreshes_once_and_preserves_request_body():
    _, handler, auth = _setup()
    seen = []

    def respond(request):
        seen.append((request.headers["Authorization"], request.content))
        return httpx.Response(401 if len(seen) == 1 else 200)

    async def body():
        yield b"same-"
        yield b"operation"

    async with httpx.AsyncClient(auth=auth, transport=httpx.MockTransport(respond)) as client:
        response = await client.post(URL, content=body())
    assert response.status_code == 200
    assert seen == [
        ("Bearer old-access", b"same-operation"),
        ("Bearer fresh-access", b"same-operation"),
    ]
    assert handler.calls == ["old-refresh"]


@pytest.mark.asyncio
async def test_concurrent_401s_consume_a_rotating_refresh_token_only_once():
    _, handler, auth = _setup()
    both_arrived = asyncio.Event()
    old_requests = 0

    async def respond(request):
        nonlocal old_requests
        if request.headers["Authorization"] == "Bearer old-access":
            old_requests += 1
            if old_requests == 2:
                both_arrived.set()
            await both_arrived.wait()
            return httpx.Response(401)
        return httpx.Response(200)

    async with httpx.AsyncClient(auth=auth, transport=httpx.MockTransport(respond)) as client:
        responses = await asyncio.wait_for(
            asyncio.gather(client.post(URL), client.post(URL)), timeout=2
        )
    assert [r.status_code for r in responses] == [200, 200]
    assert handler.calls == ["old-refresh"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [403, 404, 429, 500, 503])
async def test_non_authentication_failures_are_never_replayed(status):
    store, handler, auth = _setup()
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(status)

    async with httpx.AsyncClient(auth=auth, transport=httpx.MockTransport(respond)) as client:
        response = await client.post(URL)
    assert response.status_code == status
    assert len(calls) == 1
    assert not store.load("notion").needs_reauth
    assert handler.calls == []


@pytest.mark.asyncio
async def test_repeated_401_is_bounded():
    _, handler, auth = _setup()
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(401)

    async with httpx.AsyncClient(auth=auth, transport=httpx.MockTransport(respond)) as client:
        response = await client.post(URL)
    assert response.status_code == 401
    assert len(calls) == 2
    assert handler.calls == ["old-refresh"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error,reauth", [(RuntimeError("revoked"), True), (httpx.ConnectError("offline"), False)]
)
async def test_refresh_failure_preserves_the_grant_and_does_not_replay(error, reauth):
    store, handler, auth = _setup()
    handler.error = error
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(401)

    async with httpx.AsyncClient(auth=auth, transport=httpx.MockTransport(respond)) as client:
        response = await client.post(URL)
    assert response.status_code == 401
    assert len(calls) == 1
    assert store.load("notion").needs_reauth == reauth
    assert store.load("notion").refresh == "old-refresh"


@pytest.mark.asyncio
async def test_cancelled_request_finishes_saving_rotated_credentials():
    store, handler, auth = _setup(-1)
    handler.release = asyncio.Event()
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(200)

    async with httpx.AsyncClient(auth=auth, transport=httpx.MockTransport(respond)) as client:
        request = asyncio.create_task(client.post(URL))
        await handler.entered.wait()
        request.cancel()
        await asyncio.sleep(0)
        request.cancel()
        await asyncio.sleep(0)
        handler.release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(request, timeout=2)
    assert store.load("notion").refresh == "fresh-refresh"
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("disconnect", [True, False])
async def test_removed_or_revoked_connection_cannot_send_a_cached_token(disconnect):
    store, _, auth = _setup()
    if disconnect:
        store.delete("notion")
    else:
        store.save("notion", Tokens(access="revoked", needs_reauth=True))

    def respond(request):
        raise AssertionError("Disconnected plugin must not send a request")

    async with httpx.AsyncClient(auth=auth, transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(RuntimeError, match="reconnect in Plugins"):
            await client.post(URL)


@pytest.mark.asyncio
async def test_credentials_are_not_sent_to_a_different_origin():
    _, _, auth = _setup()

    def respond(request):
        raise AssertionError("Credentials must not leave their MCP origin")

    async with httpx.AsyncClient(auth=auth, transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(RuntimeError, match="destination"):
            await client.post("https://other.example/mcp")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "plugin_id",
    [
        plugin.id
        for plugin in load_catalog().plugins
        if (plugin.mcp_server or {}).get("transport") == "http"
    ],
)
async def test_registry_and_real_sdk_survive_rotation_mid_session(monkeypatch, plugin_id):
    """Run real SDK initialization, tools/list and tools/call through the registry."""
    import mcp.client.streamable_http as sdk

    from jarvis.marketplace.plugin_registry import PluginToolRegistry

    store, handler, _ = _setup()
    original_tokens = store.load("notion")
    store.delete("notion")
    store.save(plugin_id, original_tokens)
    remote = PluginMcpTransport()
    original = sdk.streamablehttp_client

    def open_transport(url, **kwargs):
        def http_client_factory(headers=None, timeout=None, auth=None):
            return httpx.AsyncClient(
                headers=headers,
                timeout=timeout,
                auth=auth,
                transport=httpx.MockTransport(remote),
            )

        return original(url, httpx_client_factory=http_client_factory, **kwargs)

    monkeypatch.setattr(sdk, "streamablehttp_client", open_transport)
    registry = PluginToolRegistry(
        catalog=load_catalog(),
        token_store=store,
        refresh_handler_builder=lambda _: handler,
    )
    try:
        await registry.bootstrap()
        assert registry.live_tool_count(plugin_id) == 1
        client = registry._clients[plugin_id]
        remote.accepted = "fresh-access"
        result = await client.call_tool("list_items", {})
        assert result.content[0].text == "items"
        assert remote.tool_calls == 1
        assert handler.calls == ["old-refresh"]
        assert registry._clients[plugin_id] is client
        assert not store.load(plugin_id).needs_reauth
        # Rotation by a different process is also visible without recreating SDK state.
        store.save(plugin_id, Tokens(access="external-access", refresh="external-refresh"))
        remote.accepted = "external-access"
        await registry.refresh_credentials(plugin_id)
        assert registry._clients[plugin_id] is client
        await client.call_tool("list_items", {})
        assert remote.tool_calls == 2
        assert handler.calls == ["old-refresh"]
    finally:
        await registry.stop()


@pytest.mark.asyncio
async def test_network_timeout_does_not_replay_a_potentially_completed_operation():
    _, handler, auth = _setup()
    calls = []

    def respond(request):
        calls.append(request)
        raise httpx.ReadTimeout("response lost after request was accepted")

    async with httpx.AsyncClient(auth=auth, transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(httpx.ReadTimeout):
            await client.post(URL)
    assert len(calls) == 1
    assert handler.calls == []


@pytest.mark.asyncio
async def test_expired_token_and_provider_outage_remain_retryable():
    store, handler, auth = _setup(-1)
    handler.error = httpx.ConnectError("offline")

    def respond(request):
        raise AssertionError("An expired credential must not reach the MCP server")

    async with httpx.AsyncClient(auth=auth, transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(RuntimeError, match="temporarily unavailable"):
            await client.post(URL)
    assert not store.load("notion").needs_reauth
