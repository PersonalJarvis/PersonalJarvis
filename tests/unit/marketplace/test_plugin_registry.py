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


class _FailingClient:
    """Stands in for a client whose connect-time list_tools() 401s (Bug 14)."""
    def __init__(self, spec, env_overrides=None):
        self.spec = spec
    async def start(self) -> None:
        raise RuntimeError("HTTP 401 unauthorized")
    async def stop(self) -> None: ...
    async def list_tools(self): return []


class _RecordingBus:
    def __init__(self): self.events = []
    async def publish(self, ev): self.events.append(ev)


def _store_with_calendar() -> TokenStore:
    store = TokenStore(InMemoryBackend())
    store.save("google-calendar", Tokens(access="TOK"))
    return store


@pytest.fixture
def registry_with_failing_client():
    """A registry + plugin whose client's start() raises HTTP 401 (Bug 14)."""
    catalog = PluginCatalog(version=1, schema_version="1", plugins=[_calendar_plugin()])
    reg = PluginToolRegistry(
        catalog=catalog, token_store=_store_with_calendar(),
        client_factory=_FailingClient, bus=_RecordingBus(),
    )
    return reg, catalog.plugins[0]


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


@pytest.mark.asyncio
async def test_connect_failure_is_recorded_and_tool_count_zero(registry_with_failing_client):
    reg, plugin = registry_with_failing_client  # fake client whose start()/list_tools() raises 401
    await reg._connect_plugin(plugin)
    assert reg.live_tool_count(plugin.id) == 0
    assert "401" in (reg.last_connect_error(plugin.id) or "")


# ---------------------------------------------------------------------------
# Bootstrap robustness (live 2026-07-13): one wedged plugin connect must never
# stall the whole bootstrap, and every plugin that DOES come up must reach the
# live brain immediately. Root cause of the intermittent "tool not available"
# refusals: the linear plugin's remote MCP answered 401 by hanging the
# handshake, bootstrap never finished, never published BrainToolsChanged — so
# the github plugin's 37 tools only appeared when their connect happened to
# beat the last unrelated tool refresh (mcp_autostart / cli_connected race).
# ---------------------------------------------------------------------------


def _plugin(pid: str) -> PluginSpec:
    return PluginSpec(
        id=pid, display_name=pid, description="x",
        category="Productivity", logo_slug=pid,
        auth={"mode": "pat_paste", "token_creation_url": "x", "token_prefix": "tok",
              "validation_endpoint": "x", "instruction_md": "x"},
        mcp_server={"transport": "http", "url": f"https://{pid}.example/mcp",
                    "auth_header_template": f"Authorization: Bearer $plugin_{pid}_access_token"},
    )


class _HangingClient:
    """Stands in for a wedged remote MCP handshake (401-that-hangs, dead host)."""
    def __init__(self, spec, env_overrides=None):
        self.spec = spec
    async def start(self) -> None:
        import asyncio
        await asyncio.Event().wait()  # never returns
    async def stop(self) -> None: ...
    async def list_tools(self): return []


def _factory_by_id(mapping):
    """Client factory that picks the fake class by plugin id (spec.name)."""
    def factory(spec, env_overrides=None):
        return mapping[spec.name](spec, env_overrides=env_overrides)
    return factory


def _store_for(*pids: str) -> TokenStore:
    store = TokenStore(InMemoryBackend())
    for pid in pids:
        store.save(pid, Tokens(access="TOK"))
    return store


@pytest.mark.asyncio
async def test_bootstrap_survives_hanging_plugin_connect():
    """The hanging plugin times out; the healthy one still exposes its tools,
    bootstrap completes, and BrainToolsChanged fires for the healthy plugin."""
    import asyncio

    catalog = PluginCatalog(version=1, schema_version="1",
                            plugins=[_plugin("hangs"), _plugin("healthy")])
    bus = _RecordingBus()
    reg = PluginToolRegistry(
        catalog=catalog, token_store=_store_for("hangs", "healthy"),
        client_factory=_factory_by_id({"hangs": _HangingClient, "healthy": _FakeClient}),
        bus=bus, connect_timeout_s=0.05,
    )
    await asyncio.wait_for(reg.bootstrap(), timeout=5.0)
    names = [t.name for t in reg.active_tools()]
    assert "healthy/list_events" in names
    assert reg.live_tool_count("hangs") == 0
    assert "timed out" in (reg.last_connect_error("hangs") or "")
    assert reg.is_bootstrapped()
    reasons = [getattr(e, "reason", "") for e in bus.events]
    assert "plugin_connected:healthy" in reasons


@pytest.mark.asyncio
async def test_bootstrap_publishes_one_event_per_connected_plugin():
    """Each plugin that comes up during bootstrap publishes its own
    BrainToolsChanged, so an early winner reaches the live brain immediately
    instead of waiting behind a slow or wedged later plugin."""
    catalog = PluginCatalog(version=1, schema_version="1",
                            plugins=[_plugin("one"), _plugin("two")])
    bus = _RecordingBus()
    reg = PluginToolRegistry(catalog=catalog, token_store=_store_for("one", "two"),
                             client_factory=_FakeClient, bus=bus)
    await reg.bootstrap()
    reasons = [getattr(e, "reason", "") for e in bus.events]
    assert "plugin_connected:one" in reasons
    assert "plugin_connected:two" in reasons


@pytest.mark.asyncio
async def test_auth_shaped_connect_failure_marks_needs_reauth():
    """A 401/unauthorized at connect time means the credential is dead — mark
    the token needs_reauth so later boots skip the doomed connect and the UI
    shows the Reconnect affordance."""
    catalog = PluginCatalog(version=1, schema_version="1", plugins=[_plugin("dead-token")])
    store = _store_for("dead-token")
    reg = PluginToolRegistry(catalog=catalog, token_store=store,
                             client_factory=_FailingClient, bus=_RecordingBus())
    await reg.bootstrap()
    tokens = store.load("dead-token")
    assert tokens is not None
    assert tokens.needs_reauth is True


@pytest.mark.asyncio
async def test_transient_connect_failure_does_not_mark_needs_reauth():
    """'Connection closed' and friends are retryable — one flaky boot must not
    flag a healthy plugin as needing a reconnect."""
    class _TransientFailClient(_FakeClient):
        async def start(self) -> None:
            raise RuntimeError("Connection closed")

    catalog = PluginCatalog(version=1, schema_version="1", plugins=[_plugin("flaky")])
    store = _store_for("flaky")
    reg = PluginToolRegistry(catalog=catalog, token_store=store,
                             client_factory=_TransientFailClient, bus=_RecordingBus())
    await reg.bootstrap()
    tokens = store.load("flaky")
    assert tokens is not None
    assert tokens.needs_reauth is False
    assert "Connection closed" in (reg.last_connect_error("flaky") or "")
