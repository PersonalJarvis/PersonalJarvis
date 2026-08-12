"""Loader tests: Agent Plugins v1.0.0 manifests → `PluginSpec`.

The registry auto-merges community submissions on green CI, so this loader is
the client-side re-check of every submission rule. The rejection cases below
are the threat table from docs/marketplace/public-marketplace-analysis.md §4.
"""

from __future__ import annotations

from typing import Any

import pytest

from jarvis.marketplace.agent_plugins_loader import (
    EXTENSION_NAMESPACE,
    AgentPluginError,
    convert_manifest,
    validate_spec_name,
)


def _plugin_json(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
        "name": "todo-fox",
        "description": "Tasks and reminders from TodoFox",
        "version": "1.2.0",
        "license": "MIT",
        "extensions": {
            EXTENSION_NAMESPACE: {
                "display_name": "TodoFox",
                "category": "Lists & Tasks",
                "logo_slug": "todofox",
                "auth": {
                    "mode": "pat_paste",
                    "token_creation_url": "https://todofox.example/settings/tokens",
                    "token_prefix": "tfx_",
                    "validation_endpoint": "https://api.todofox.example/v1/me",
                    "instruction_md": "Create a token in Settings.",
                },
                "mcp_auth_header_template": (
                    "Authorization: Bearer ${plugin_todo-fox_access_token}"
                ),
            }
        },
    }
    base.update(overrides)
    return base


def _mcp_json_http() -> dict[str, Any]:
    return {
        "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
        "mcpServers": {
            "todo-fox": {
                "type": "streamable-http",
                "url": "https://mcp.todofox.example/mcp",
            }
        },
    }


def _mcp_json_stdio() -> dict[str, Any]:
    return {
        "mcpServers": {
            "todo-fox": {
                "type": "stdio",
                "command": "npx",
                "args": ["-y", "@todofox/mcp-server@1.2.0"],
                "env": {"TODOFOX_TOKEN": "$plugin_todo_fox_access_token"},
            }
        }
    }


def test_valid_hosted_manifest_converts() -> None:
    spec = convert_manifest(
        _plugin_json(),
        _mcp_json_http(),
        publisher="octocat",
        version="1.2.0",
        source_url="https://github.com/PersonalJarvis/marketplace/tree/main/plugins/todo-fox",
    )
    assert spec.id == "todo-fox"
    assert spec.display_name == "TodoFox"
    assert spec.source == "community"
    assert spec.publisher == "octocat"
    assert spec.version == "1.2.0"
    assert spec.featured is False
    assert spec.auth.mode == "pat_paste"
    assert spec.mcp_server == {
        "transport": "http",
        "url": "https://mcp.todofox.example/mcp",
        "auth_header_template": "Authorization: Bearer ${plugin_todo-fox_access_token}",
    }


def test_valid_stdio_manifest_converts() -> None:
    spec = convert_manifest(_plugin_json(), _mcp_json_stdio())
    assert spec.mcp_server == {
        "transport": "stdio",
        "install": ["npx", "-y", "@todofox/mcp-server@1.2.0"],
        "env_template": {"TODOFOX_TOKEN": "$plugin_todo_fox_access_token"},
    }


def test_manifest_without_mcp_json_is_metadata_only() -> None:
    spec = convert_manifest(_plugin_json())
    assert spec.mcp_server is None


def test_defaults_fill_missing_branding() -> None:
    manifest = _plugin_json()
    extension = manifest["extensions"][EXTENSION_NAMESPACE]
    del extension["display_name"], extension["category"], extension["logo_slug"]
    spec = convert_manifest(manifest)
    assert spec.display_name == "Todo Fox"
    assert spec.category == "Community"
    assert spec.logo_slug == "todo-fox"


@pytest.mark.parametrize(
    "bad_name",
    ["Todo_Fox", "-todofox", "todofox-", "todo--fox", "todo..fox", "", "a" * 65],
)
def test_spec_name_rules_reject(bad_name: str) -> None:
    with pytest.raises(AgentPluginError):
        validate_spec_name(bad_name)


def test_missing_extension_namespace_rejected() -> None:
    with pytest.raises(AgentPluginError, match=EXTENSION_NAMESPACE.replace(".", r"\.")):
        convert_manifest(_plugin_json(extensions={}))


def test_missing_auth_rejected() -> None:
    manifest = _plugin_json()
    del manifest["extensions"][EXTENSION_NAMESPACE]["auth"]
    with pytest.raises(AgentPluginError, match="auth"):
        convert_manifest(manifest)


def test_credentials_in_headers_rejected() -> None:
    mcp = _mcp_json_http()
    mcp["mcpServers"]["todo-fox"]["headers"] = {"Authorization": "Bearer sk-live-123"}
    with pytest.raises(AgentPluginError, match="headers"):
        convert_manifest(_plugin_json(), mcp)


def test_literal_token_in_header_template_rejected() -> None:
    manifest = _plugin_json()
    manifest["extensions"][EXTENSION_NAMESPACE]["mcp_auth_header_template"] = (
        "Authorization: Bearer ghp_0123456789abcdef0123456789abcdef"
    )
    with pytest.raises(AgentPluginError, match="placeholder"):
        convert_manifest(manifest, _mcp_json_http())


def test_plain_http_url_rejected() -> None:
    mcp = _mcp_json_http()
    mcp["mcpServers"]["todo-fox"]["url"] = "http://mcp.todofox.example/mcp"
    with pytest.raises(AgentPluginError, match="https"):
        convert_manifest(_plugin_json(), mcp)


def test_http_url_inside_auth_rejected() -> None:
    manifest = _plugin_json()
    bad_url = "http://todofox.example/tokens"
    manifest["extensions"][EXTENSION_NAMESPACE]["auth"]["token_creation_url"] = bad_url  # noqa: S105
    with pytest.raises(AgentPluginError, match="non-https"):
        convert_manifest(manifest)


def test_disallowed_launcher_rejected() -> None:
    mcp = _mcp_json_stdio()
    mcp["mcpServers"]["todo-fox"]["command"] = "powershell"
    with pytest.raises(AgentPluginError, match="launcher"):
        convert_manifest(_plugin_json(), mcp)


def test_unpinned_stdio_package_rejected() -> None:
    mcp = _mcp_json_stdio()
    mcp["mcpServers"]["todo-fox"]["args"] = ["-y", "@todofox/mcp-server@latest"]
    with pytest.raises(AgentPluginError, match="unpinned"):
        convert_manifest(_plugin_json(), mcp)


def test_literal_env_value_rejected() -> None:
    mcp = _mcp_json_stdio()
    mcp["mcpServers"]["todo-fox"]["env"] = {"TODOFOX_TOKEN": "tfx_realtoken123"}
    with pytest.raises(AgentPluginError, match="placeholder"):
        convert_manifest(_plugin_json(), mcp)


def test_sse_transport_rejected() -> None:
    mcp = _mcp_json_http()
    mcp["mcpServers"]["todo-fox"]["type"] = "sse"
    with pytest.raises(AgentPluginError, match="sse"):
        convert_manifest(_plugin_json(), mcp)


def test_multiple_unnamed_servers_rejected() -> None:
    mcp = _mcp_json_http()
    mcp["mcpServers"]["other"] = dict(mcp["mcpServers"]["todo-fox"])
    mcp["mcpServers"].pop("todo-fox")
    mcp["mcpServers"]["second"] = dict(mcp["mcpServers"]["other"])
    with pytest.raises(AgentPluginError, match="exactly one"):
        convert_manifest(_plugin_json(), mcp)


def test_native_tool_claim_rejected() -> None:
    manifest = _plugin_json()
    manifest["extensions"][EXTENSION_NAMESPACE]["native_tool"] = "gmail"
    with pytest.raises(AgentPluginError, match="native_tool"):
        convert_manifest(manifest)


def test_invalid_auth_mode_maps_to_readable_error() -> None:
    manifest = _plugin_json()
    manifest["extensions"][EXTENSION_NAMESPACE]["auth"] = {"mode": "made-up"}
    with pytest.raises(AgentPluginError, match="auth"):
        convert_manifest(manifest)
