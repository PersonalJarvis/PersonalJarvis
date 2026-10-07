"""Reconnect errors must neither hide authentication nor overwrite a newer grant."""

from __future__ import annotations

import httpx
import pytest

from jarvis.marketplace.catalog import PluginCatalog
from jarvis.marketplace.catalog_data import load_catalog
from jarvis.marketplace.plugin_registry import PluginToolRegistry
from jarvis.marketplace.token_store import InMemoryBackend, Tokens, TokenStore
from tests.fakes.fake_plugin_http import RotatingPluginHandler


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 503])
async def test_nested_sdk_http_errors_select_refresh_or_retry(status):
    plugin = load_catalog().by_id("notion")
    store = TokenStore(InMemoryBackend())
    store.save(plugin.id, Tokens(access="old-access", refresh="old-refresh"))
    handler = RotatingPluginHandler()

    class Client:
        def __init__(self, spec, **kwargs):
            self.spec = spec

        async def start(self):
            if self.spec.headers["Authorization"] == "Bearer old-access":
                response = httpx.Response(status, request=httpx.Request("POST", self.spec.url))
                error = httpx.HTTPStatusError(
                    f"HTTP {status}", request=response.request, response=response
                )
                raise ExceptionGroup("TaskGroup", [ExceptionGroup("transport", [error])])

        async def stop(self):
            pass

        async def list_tools(self):
            return [{"name": "list_items", "inputSchema": {}}]

    registry = PluginToolRegistry(
        catalog=PluginCatalog(version=1, schema_version="1", plugins=[plugin]),
        token_store=store,
        client_factory=Client,
        refresh_handler_builder=lambda _: handler,
    )
    try:
        await registry.bootstrap()
        assert handler.calls == (["old-refresh"] if status == 401 else [])
        assert registry.live_tool_count(plugin.id) == (1 if status == 401 else 0)
        assert not store.load(plugin.id).needs_reauth
        if status == 503:
            assert plugin.id in registry._retry_tasks
    finally:
        await registry.stop()


@pytest.mark.asyncio
async def test_reauth_mark_is_atomic_with_concurrent_credential_rotation():
    replacement = Tokens(access="new-login", refresh="new-refresh")

    class RacingStore(TokenStore):
        def compare_and_save(self, plugin_id, expected, updated):
            # Another instance commits between the caller's load and its write.
            self.save(plugin_id, replacement)
            return super().compare_and_save(plugin_id, expected, updated)

    store = RacingStore(InMemoryBackend())
    original = Tokens(access="old-access", refresh="old-refresh")
    store.save("notion", original)
    registry = PluginToolRegistry(token_store=store)
    try:
        registry._maybe_mark_needs_reauth("notion", "HTTP 401", expected_tokens=original)
        assert store.load("notion") == replacement
    finally:
        await registry.stop()


@pytest.mark.asyncio
async def test_stale_auth_refusal_does_not_flag_a_reconnected_static_token():
    original = Tokens(access="old-access")
    replacement = Tokens(access="new-access")
    store = TokenStore(InMemoryBackend())
    store.save("notion", replacement)
    registry = PluginToolRegistry(token_store=store)
    try:
        registry._maybe_mark_needs_reauth("notion", "HTTP 401", expected_tokens=original)
        assert store.load("notion") == replacement
    finally:
        await registry.stop()
