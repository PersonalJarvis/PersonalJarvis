import pytest

from jarvis.marketplace import plugin_shared
from jarvis.marketplace.catalog import PluginCatalog, PluginSpec
from jarvis.marketplace.plugin_registry import PluginToolRegistry
from jarvis.marketplace.token_store import InMemoryBackend, Tokens, TokenStore


class _FakeClient:
    def __init__(self, spec, env_overrides=None): self.spec = spec
    async def start(self): ...
    async def stop(self): ...
    async def list_tools(self):
        return [{"name": "list_events", "description": "List", "inputSchema": {}}]


def _plugin():
    return PluginSpec(
        id="google-calendar", display_name="Google Calendar", description="Cal",
        category="Productivity", logo_slug="googlecalendar",
        auth={"mode": "pat_paste", "token_creation_url": "x", "token_prefix": "ya29",
              "validation_endpoint": "x", "instruction_md": "x"},
        mcp_server={"transport": "http", "url": "https://cal/mcp",
                    "auth_header_template": "Authorization: Bearer $plugin_google-calendar_access_token"})


@pytest.mark.asyncio
async def test_connect_then_refresh_exposes_tool_via_loader():
    from jarvis.marketplace.plugin_loader import PluginToolLoader

    store = TokenStore(InMemoryBackend())
    catalog = PluginCatalog(version=1, schema_version="1", plugins=[_plugin()])

    class _Bus:
        async def publish(self, ev): ...

    reg = PluginToolRegistry(catalog=catalog, token_store=store,
                             client_factory=_FakeClient, bus=_Bus())
    plugin_shared.set_active_plugin_registry(reg)
    try:
        await reg.bootstrap()
        assert PluginToolLoader().expand() == []          # nothing connected yet
        store.save("google-calendar", Tokens(access="TOK"))
        await reg.refresh_plugin("google-calendar")
        names = [t.name for t in PluginToolLoader().expand()]
        assert "google-calendar/list_events" in names     # now live, no restart
    finally:
        plugin_shared.set_active_plugin_registry(None)
