"""Network recovery must reuse grants without prompting for a new login."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from jarvis.marketplace.catalog import PluginCatalog
from jarvis.marketplace.plugin_registry import PluginToolRegistry
from jarvis.marketplace.refresh_scheduler import refresh_plugin_token
from jarvis.marketplace.token_store import InMemoryBackend, Tokens, TokenStore
from tests.unit.marketplace.test_plugin_registry import _calendar_plugin, _FakeClient


@pytest.mark.asyncio
async def test_pat_recovers_after_network_returns_without_new_authorization():
    store = TokenStore(InMemoryBackend())
    original = Tokens(access="synthetic-pat")
    store.save("google-calendar", original)
    attempts = []

    class OfflineOnce(_FakeClient):
        async def start(self):
            attempts.append(self)
            if len(attempts) == 1:
                raise ConnectionError("network unavailable")

    registry = PluginToolRegistry(
        catalog=PluginCatalog(version=1, schema_version="1", plugins=[_calendar_plugin()]),
        token_store=store,
        client_factory=OfflineOnce,
        retry_initial_s=0.01,
    )
    try:
        await registry.bootstrap()
        assert registry.live_tool_count("google-calendar") == 0
        async with asyncio.timeout(3):
            while not registry.active_tools():  # noqa: ASYNC110 - observe the public registry state
                await asyncio.sleep(0.01)
        assert len(attempts) == 2
        assert store.load("google-calendar") == original
        assert registry.last_connect_error("google-calendar") is None
    finally:
        await registry.stop()
    assert not registry._retry_tasks


@pytest.mark.asyncio
async def test_disconnect_during_retry_never_restores_the_connection():
    store = TokenStore(InMemoryBackend())
    store.save("google-calendar", Tokens(access="synthetic-pat"))
    calls = []

    class Offline(_FakeClient):
        async def start(self):
            calls.append(1)
            raise ConnectionError("network unavailable")

    registry = PluginToolRegistry(
        catalog=PluginCatalog(version=1, schema_version="1", plugins=[_calendar_plugin()]),
        token_store=store,
        client_factory=Offline,
        retry_initial_s=0.01,
    )
    await registry.bootstrap()
    store.delete("google-calendar")
    await registry.refresh_plugin("google-calendar")
    await asyncio.sleep(0.1)
    await registry.stop()
    assert len(calls) == 1
    assert not registry.active_tools()
    assert not registry._retry_tasks


@pytest.mark.asyncio
async def test_stop_cancels_pending_retries():
    store = TokenStore(InMemoryBackend())
    store.save("google-calendar", Tokens(access="synthetic-pat"))

    class Offline(_FakeClient):
        async def start(self):
            raise ConnectionError("network unavailable")

    registry = PluginToolRegistry(
        catalog=PluginCatalog(version=1, schema_version="1", plugins=[_calendar_plugin()]),
        token_store=store,
        client_factory=Offline,
    )
    await registry.bootstrap()
    tasks = list(registry._retry_tasks.values())
    assert tasks
    await registry.stop()
    assert all(task.done() for task in tasks)
    assert not registry._retry_tasks


@pytest.mark.asyncio
async def test_expired_nonrefreshable_token_is_flagged_without_provider_call():
    store = TokenStore(InMemoryBackend())
    store.save(
        "linkedin", Tokens(access="synthetic", expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )

    def unexpected(_):
        pytest.fail("A non-refreshable token must not call the provider")

    result = await refresh_plugin_token("linkedin", store, unexpected)
    assert not result.usable
    assert store.load("linkedin").reauth_reason == "refresh_missing"


def test_refresh_missing_reason_crosses_storage_and_ui_contract():
    from pathlib import Path

    tokens = Tokens("synthetic", needs_reauth=True, reauth_reason="refresh_missing")
    assert Tokens.from_json(tokens.to_json()).reauth_reason == "refresh_missing"
    frontend = Path(__file__).parents[3] / "jarvis/ui/web/frontend/src/views/PluginsView.tsx"
    assert '"refresh_missing"' in frontend.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_shutdown_drains_a_retry_that_is_rotating_credentials():
    entered, release = asyncio.Event(), asyncio.Event()
    store = TokenStore(InMemoryBackend())
    store.save("google-calendar", Tokens("old", "refresh"))
    starts = []

    class Recovering(_FakeClient):
        async def start(self):
            starts.append(1)
            if len(starts) == 1:
                raise ConnectionError("network unavailable")
            if len(starts) == 2:
                raise RuntimeError("HTTP 401 unauthorized")

    class Rotating:
        async def refresh(self, tokens):
            entered.set()
            await release.wait()
            return Tokens("new", "rotated")

    registry = PluginToolRegistry(
        catalog=PluginCatalog(version=1, schema_version="1", plugins=[_calendar_plugin()]),
        token_store=store,
        client_factory=Recovering,
        retry_initial_s=0.01,
        refresh_handler_builder=lambda _: Rotating(),
    )
    await registry.bootstrap()
    await asyncio.wait_for(entered.wait(), timeout=3)
    stopping = asyncio.create_task(registry.stop())
    await asyncio.sleep(0)
    assert not stopping.done()
    release.set()
    await asyncio.wait_for(stopping, timeout=3)
    assert store.load("google-calendar").refresh == "rotated"
    assert not registry.active_tools()
    assert not registry._retry_tasks
