"""plugin_to_mcp_server_spec maps a connected plugin's mcp_server dict + token
into the in-process MCPServerSpec the MCPClient consumes."""
from jarvis.marketplace.plugin_mcp import plugin_to_mcp_server_spec
from jarvis.marketplace.catalog import PluginSpec
from jarvis.marketplace.token_store import Tokens


def _spec(mcp_server) -> PluginSpec:
    return PluginSpec(
        id="google-calendar",
        display_name="Google Calendar",
        description="Calendar",
        category="Productivity",
        logo_slug="googlecalendar",
        auth={"mode": "pat_paste", "token_creation_url": "x", "token_prefix": "ya29",
              "validation_endpoint": "x", "instruction_md": "x"},
        mcp_server=mcp_server,
    )


def test_http_transport_resolves_bearer_header():
    plugin = _spec({
        "transport": "http",
        "url": "https://cal.example/mcp",
        "auth_header_template": "Authorization: Bearer $plugin_google-calendar_access_token",
    })
    result = plugin_to_mcp_server_spec(plugin, Tokens(access="TOK123"))
    assert result is not None
    server_spec, env_overrides = result
    assert server_spec.transport == "http"
    assert server_spec.url == "https://cal.example/mcp"
    assert server_spec.headers == {"Authorization": "Bearer TOK123"}
    assert env_overrides == {}


def test_stdio_transport_resolves_install_and_env():
    plugin = _spec({
        "transport": "stdio",
        "install": ["npx", "-y", "@calendar/mcp", "--token", "$plugin_google-calendar_access_token"],
        "env_template": {"CAL_TOKEN": "${plugin_google-calendar_access_token}"},
    })
    result = plugin_to_mcp_server_spec(plugin, Tokens(access="TOK123"))
    assert result is not None
    server_spec, env_overrides = result
    assert server_spec.transport == "stdio"
    assert server_spec.install_command == ["npx", "-y", "@calendar/mcp", "--token", "TOK123"]
    assert env_overrides == {"CAL_TOKEN": "TOK123"}


def test_unsupported_transport_returns_none():
    plugin = _spec({"transport": "rest_wrapper", "url": "x"})
    assert plugin_to_mcp_server_spec(plugin, Tokens(access="TOK123")) is None


def test_no_mcp_server_returns_none():
    plugin = _spec(None)
    assert plugin_to_mcp_server_spec(plugin, Tokens(access="TOK123")) is None
