import pytest

from jarvis.marketplace.catalog import PluginCatalog, PluginSpec
from jarvis.marketplace.plugin_registry import PluginToolRegistry
from jarvis.marketplace.token_store import InMemoryBackend, Tokens, TokenStore


def _calendar_plugin() -> PluginSpec:
    return PluginSpec(
        id="google-calendar", display_name="Google Calendar", description="Calendar",
        category="Productivity", logo_slug="googlecalendar",
        auth={"mode": "pat_paste", "token_creation_url": "x", "token_prefix": "ya29",
              "validation_endpoint": "x", "instruction_md": "x"},
        mcp_server={"transport": "http", "url": "https://cal/mcp",
                    "auth_header_template": "Authorization: Bearer $plugin_google-calendar_access_token"},
    )


class _FakeClient:
    """Stands in for jarvis.mcp.MCPClient — no network."""
    def __init__(self, spec, env_overrides=None):
        self.spec = spec
        self._tools = [{"name": "list_events", "description": "List events", "inputSchema": {}}]
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def list_tools(self): return list(self._tools)


class _RecordingBus:
    def __init__(self): self.events = []
    async def publish(self, ev): self.events.append(ev)


def _store_with_calendar() -> TokenStore:
    store = TokenStore(InMemoryBackend())
    store.save("google-calendar", Tokens(access="TOK"))
    return store


@pytest.mark.asyncio
async def test_bootstrap_exposes_connected_plugin_tools():
    catalog = PluginCatalog(version=1, schema_version="1", plugins=[_calendar_plugin()])
    bus = _RecordingBus()
    reg = PluginToolRegistry(
        catalog=catalog, token_store=_store_with_calendar(),
        client_factory=_FakeClient, bus=bus,
    )
    await reg.bootstrap()
    names = [t.name for t in reg.active_tools()]
    assert "google-calendar/list_events" in names
    assert any(type(e).__name__ == "BrainToolsChanged" for e in bus.events)


@pytest.mark.asyncio
async def test_no_token_means_no_tools():
    catalog = PluginCatalog(version=1, schema_version="1", plugins=[_calendar_plugin()])
    reg = PluginToolRegistry(
        catalog=catalog, token_store=TokenStore(InMemoryBackend()),
        client_factory=_FakeClient, bus=_RecordingBus(),
    )
    await reg.bootstrap()
    assert reg.active_tools() == []


@pytest.mark.asyncio
async def test_refresh_plugin_disconnect_removes_tools():
    catalog = PluginCatalog(version=1, schema_version="1", plugins=[_calendar_plugin()])
    store = _store_with_calendar()
    reg = PluginToolRegistry(catalog=catalog, token_store=store,
                             client_factory=_FakeClient, bus=_RecordingBus())
    await reg.bootstrap()
    assert reg.active_tools()
    store.delete("google-calendar")
    await reg.refresh_plugin("google-calendar")
    assert reg.active_tools() == []


@pytest.mark.asyncio
async def test_bootstrap_without_bus_does_not_raise():
    catalog = PluginCatalog(version=1, schema_version="1", plugins=[_calendar_plugin()])
    reg = PluginToolRegistry(catalog=catalog, token_store=_store_with_calendar(),
                             client_factory=_FakeClient, bus=None)
    await reg.bootstrap()
    assert reg.active_tools()


@pytest.mark.asyncio
async def test_needs_reauth_token_is_skipped():
    catalog = PluginCatalog(version=1, schema_version="1", plugins=[_calendar_plugin()])
    store = TokenStore(InMemoryBackend())
    store.save("google-calendar", Tokens(access="TOK", needs_reauth=True))
    reg = PluginToolRegistry(catalog=catalog, token_store=store,
                             client_factory=_FakeClient, bus=_RecordingBus())
    await reg.bootstrap()
    assert reg.active_tools() == []


@pytest.mark.asyncio
async def test_refresh_reconnect_publishes_connected_event():
    catalog = PluginCatalog(version=1, schema_version="1", plugins=[_calendar_plugin()])
    store = TokenStore(InMemoryBackend())
    bus = _RecordingBus()
    reg = PluginToolRegistry(catalog=catalog, token_store=store,
                             client_factory=_FakeClient, bus=bus)
    await reg.bootstrap()
    assert reg.active_tools() == []          # nothing connected at boot
    store.save("google-calendar", Tokens(access="TOK"))
    await reg.refresh_plugin("google-calendar")
    assert reg.active_tools()                # now live
    reasons = [getattr(e, "reason", "") for e in bus.events]
    assert "plugin_connected:google-calendar" in reasons


@pytest.mark.asyncio
async def test_concurrent_bootstrap_and_refresh_no_corruption():
    """bootstrap() and refresh_plugin() fired concurrently (server start vs a
    REST connect during boot) must not double-register tools or leak clients —
    the asyncio.Lock serialises them and _connect_plugin is idempotent."""
    import asyncio

    class _SlowClient(_FakeClient):
        async def start(self) -> None:
            await asyncio.sleep(0.01)  # widen the interleave window

    catalog = PluginCatalog(version=1, schema_version="1", plugins=[_calendar_plugin()])
    reg = PluginToolRegistry(catalog=catalog, token_store=_store_with_calendar(),
                             client_factory=_SlowClient, bus=_RecordingBus())
    await asyncio.gather(reg.bootstrap(), reg.refresh_plugin("google-calendar"))
    names = [t.name for t in reg.active_tools()]
    assert names.count("google-calendar/list_events") == 1   # not doubled
    assert list(reg._clients) == ["google-calendar"]          # exactly one, not leaked
