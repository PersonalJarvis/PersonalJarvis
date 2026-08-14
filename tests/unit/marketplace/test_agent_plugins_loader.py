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
    MAX_BUNDLED_SKILLS,
    MAX_SKILL_MD_BYTES,
    AgentPluginError,
    convert_manifest,
    convert_package,
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


def test_manifest_without_any_component_is_rejected() -> None:
    """A card that collects a token and offers nothing is not a plugin.

    The manifest alone is valid under the spec (components are optional), but
    for a COMMUNITY entry the three ways to be useful are an MCP server, a
    hosted MCP auth mode, and bundled skills — native bindings are blocked.
    Without one of them the store would show a connectable card with no tools
    behind it.
    """
    with pytest.raises(AgentPluginError, match="no components"):
        convert_manifest(_plugin_json())


def test_defaults_fill_missing_branding() -> None:
    manifest = _plugin_json()
    extension = manifest["extensions"][EXTENSION_NAMESPACE]
    del extension["display_name"], extension["category"], extension["logo_slug"]
    spec = convert_manifest(manifest, _mcp_json_http())
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


# --- Schema conformance ------------------------------------------------------


def test_missing_schema_declaration_rejected() -> None:
    """`$schema` is REQUIRED by the spec and pins the format version.

    The spec forbids reassigning a published schema id to different contents,
    so an equality check is how a client states which format it understands —
    a manifest without it could be any future revision.
    """
    manifest = _plugin_json()
    del manifest["$schema"]
    with pytest.raises(AgentPluginError, match=r"\$schema"):
        convert_manifest(manifest, _mcp_json_http())


def test_foreign_schema_version_rejected() -> None:
    manifest = _plugin_json(
        **{"$schema": "https://agent-plugins.org/schemas/2.0.0/plugin.schema.json"}
    )
    with pytest.raises(AgentPluginError, match=r"\$schema"):
        convert_manifest(manifest, _mcp_json_http())


# --- Bundled skills ----------------------------------------------------------


def _skill_md(name: str = "todo-triage", extra: str = "") -> str:
    return (
        "---\n"
        'schema_version: "1"\n'
        f"name: {name}\n"
        "description: Sort the inbox into today and later.\n"
        f"{extra}"
        "---\n\n"
        "Group open tasks by due date before answering.\n"
    )


def test_bundled_skill_rides_along_with_the_plugin() -> None:
    package = convert_package(
        _plugin_json(),
        _mcp_json_http(),
        [{"name": "todo-triage", "skill_md": _skill_md()}],
    )
    assert [skill.name for skill in package.skills] == ["todo-triage"]
    # Recorded on the catalog entry so uninstall knows what it owns.
    assert package.spec.bundled_skills == ["todo-triage"]


def test_bundled_skill_may_not_declare_its_own_risk_policy() -> None:
    """The privilege boundary: skills/runner.py evaluates a skill's tools
    against the SKILL'S declared tier, not the tool's own. An auto-merged
    community skill that sets `default_tier: safe` would skip exactly the
    confirmation the tool was given."""
    skill = _skill_md(extra="risk_policy:\n  default_tier: safe\n")
    with pytest.raises(AgentPluginError, match="risk_policy"):
        convert_package(
            _plugin_json(), _mcp_json_http(), [{"name": "todo-triage", "skill_md": skill}]
        )


def test_bundled_skill_name_must_be_a_slug() -> None:
    """The name becomes a directory under the user's skills root."""
    with pytest.raises(AgentPluginError, match="name"):
        convert_package(
            _plugin_json(),
            _mcp_json_http(),
            [{"name": "../../evil", "skill_md": _skill_md()}],
        )


def test_bundled_skill_without_frontmatter_rejected() -> None:
    with pytest.raises(AgentPluginError, match="frontmatter"):
        convert_package(
            _plugin_json(),
            _mcp_json_http(),
            [{"name": "todo-triage", "skill_md": "Just prose, no frontmatter.\n"}],
        )


def test_bundled_skill_count_is_capped() -> None:
    skills = [
        {"name": f"todo-triage-{index}", "skill_md": _skill_md(f"todo-triage-{index}")}
        for index in range(MAX_BUNDLED_SKILLS + 1)
    ]
    with pytest.raises(AgentPluginError, match="at most"):
        convert_package(_plugin_json(), _mcp_json_http(), skills)


def test_oversized_bundled_skill_rejected() -> None:
    fat = _skill_md() + ("x" * (MAX_SKILL_MD_BYTES + 1))
    with pytest.raises(AgentPluginError, match="exceeds"):
        convert_package(
            _plugin_json(), _mcp_json_http(), [{"name": "todo-triage", "skill_md": fat}]
        )


def test_duplicate_bundled_skill_names_rejected() -> None:
    item = {"name": "todo-triage", "skill_md": _skill_md()}
    with pytest.raises(AgentPluginError, match="twice"):
        convert_package(_plugin_json(), _mcp_json_http(), [item, dict(item)])


def test_skills_alone_satisfy_the_component_rule() -> None:
    """A package whose only component is skills is still useful, so the
    no-components check must not fire on it."""
    package = convert_package(
        _plugin_json(), None, [{"name": "todo-triage", "skill_md": _skill_md()}]
    )
    assert package.spec.mcp_server is None
    assert package.spec.bundled_skills == ["todo-triage"]
