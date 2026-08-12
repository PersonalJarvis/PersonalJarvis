"""Convert an Agent Plugins v1.0.0 manifest set into a catalog `PluginSpec`.

The community registry distributes plugins as Agent Plugins v1.0.0 packages
(docs/marketplace/agent-plugins-standard.md): a `plugin.json` whose Jarvis
specifics live under ``extensions["io.github.personaljarvis"]``, plus an
optional `mcp.json`. This module is the "loader wave" that document defers:
it validates the manifests and produces the same `PluginSpec` the seed
catalog uses, so an installed community plugin flows through the exact same
runtime (connect flows, relevance gate, worker bridge) as a shipped one.

Security posture: the registry auto-merges submissions on green CI, so the
client must NOT blindly trust index content. Every rule CI enforces on
submission is re-enforced here — spec-conformant name, https-only endpoints,
launcher allowlist for stdio commands, pinned package versions, and no
credentials in `mcp.json` headers or env. A violation raises
:class:`AgentPluginError` with a message safe to surface in the UI.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from jarvis.marketplace.catalog import PluginSpec

EXTENSION_NAMESPACE = "io.github.personaljarvis"

# Spec name constraints (agent-plugins.org 1.0.0): 1-64 chars, lowercase
# alphanumerics plus `-` and `.`, alphanumeric start and end, no `--`/`..`,
# underscores illegal.
_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,62}[a-z0-9])?$")

# The only stdio launchers a community manifest may use. Anything else is an
# arbitrary command line on the user's machine.
_STDIO_LAUNCHERS = ("npx", "uvx", "docker")

# Token placeholders the runtime resolves at connect time. Everything else in
# an env value is treated as a smuggled literal credential. Braces must be
# MATCHED (both or neither), and the charset includes '-'/'.' because plugin
# ids may carry them ("$plugin_todo-fox_access_token").
_ENV_PLACEHOLDER_RE = re.compile(r"^\$(?:plugin_[a-z0-9_.-]+|\{plugin_[a-z0-9_.-]+\})$")
_HEADER_PLACEHOLDER_RE = re.compile(r"\$(?:plugin_[a-z0-9_.-]+|\{plugin_[a-z0-9_.-]+\})")
# A long unbroken token-ish run left over once placeholders are removed.
_TOKEN_LITERAL_RE = re.compile(r"[A-Za-z0-9_\-]{20,}")


class AgentPluginError(ValueError):
    """A manifest violates the Agent Plugins spec or the community rules."""


def _require_str(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AgentPluginError(f"{what} must be a non-empty string")
    return value.strip()


def validate_spec_name(name: Any) -> str:
    text = _require_str(name, "plugin name")
    if not _NAME_RE.fullmatch(text) or "--" in text or ".." in text:
        raise AgentPluginError(
            f"plugin name {text!r} violates the Agent Plugins name rules "
            "(1-64 chars, a-z 0-9 - . only, alphanumeric start/end, "
            "no '--' or '..', no underscores)"
        )
    return text


def _reject_http_urls(value: Any, where: str) -> None:
    """Recursively refuse plaintext-http endpoints anywhere in a block."""
    if isinstance(value, str):
        if value.lstrip().lower().startswith("http://"):
            raise AgentPluginError(f"{where} contains a non-https URL: {value!r}")
        return
    if isinstance(value, Mapping):
        for key, sub in value.items():
            _reject_http_urls(sub, f"{where}.{key}")
        return
    if isinstance(value, list):
        for index, sub in enumerate(value):
            _reject_http_urls(sub, f"{where}[{index}]")


def _validate_auth_header_template(template: str) -> str:
    """The template must inject a token PLACEHOLDER, never a literal token."""
    if not _HEADER_PLACEHOLDER_RE.search(template):
        raise AgentPluginError("mcp_auth_header_template must reference a $plugin_… placeholder")
    remainder = _HEADER_PLACEHOLDER_RE.sub(" ", template)
    if _TOKEN_LITERAL_RE.search(remainder):
        raise AgentPluginError("mcp_auth_header_template may not embed literal credentials")
    return template


def _convert_http_server(
    name: str, server: Mapping[str, Any], extension: Mapping[str, Any]
) -> dict[str, Any]:
    url = _require_str(server.get("url"), f"mcp.json server {name!r}: url")
    if not url.lower().startswith("https://"):
        raise AgentPluginError(f"mcp.json server {name!r}: url must be https:// (got {url!r})")
    if server.get("headers"):
        # The spec forbids credentials in headers; community manifests get the
        # stricter rule (no headers at all) because a static header IS how a
        # token would be smuggled past review.
        raise AgentPluginError(
            f"mcp.json server {name!r}: headers are not allowed — token "
            "injection is a client concern (extension namespace)"
        )
    mcp_server: dict[str, Any] = {"transport": "http", "url": url}
    template = extension.get("mcp_auth_header_template")
    if template is not None:
        mcp_server["auth_header_template"] = _validate_auth_header_template(
            _require_str(template, "mcp_auth_header_template")
        )
    return mcp_server


def _convert_stdio_server(name: str, server: Mapping[str, Any]) -> dict[str, Any]:
    command = _require_str(server.get("command"), f"mcp.json server {name!r}: command")
    launcher = command.replace("\\", "/").rsplit("/", 1)[-1].lower()
    launcher = launcher.removesuffix(".exe").removesuffix(".cmd")
    if launcher not in _STDIO_LAUNCHERS:
        raise AgentPluginError(
            f"mcp.json server {name!r}: launcher {command!r} is not allowed "
            f"(community stdio servers must use one of {', '.join(_STDIO_LAUNCHERS)})"
        )
    raw_args = server.get("args", [])
    if not isinstance(raw_args, list) or not all(isinstance(a, str) for a in raw_args):
        raise AgentPluginError(f"mcp.json server {name!r}: args must be strings")
    for arg in raw_args:
        if arg.endswith("@latest"):
            raise AgentPluginError(
                f"mcp.json server {name!r}: {arg!r} is unpinned — community "
                "stdio packages must pin an exact version"
            )
    env_template: dict[str, str] = {}
    raw_env = server.get("env", {})
    if not isinstance(raw_env, Mapping):
        raise AgentPluginError(f"mcp.json server {name!r}: env must be a mapping")
    for key, value in raw_env.items():
        if not isinstance(value, str) or not _ENV_PLACEHOLDER_RE.fullmatch(value):
            raise AgentPluginError(
                f"mcp.json server {name!r}: env {key!r} must be a "
                "$plugin_… placeholder, never a literal value"
            )
        env_template[str(key)] = value
    return {
        "transport": "stdio",
        "install": [launcher, *raw_args],
        "env_template": env_template,
    }


def _convert_mcp_json(
    plugin_name: str, mcp_json: Mapping[str, Any], extension: Mapping[str, Any]
) -> dict[str, Any]:
    servers = mcp_json.get("mcpServers")
    if not isinstance(servers, Mapping) or not servers:
        raise AgentPluginError("mcp.json must declare a non-empty mcpServers map")
    if plugin_name in servers:
        server_name, server = plugin_name, servers[plugin_name]
    elif len(servers) == 1:
        server_name, server = next(iter(servers.items()))
    else:
        raise AgentPluginError(
            "mcp.json declares several servers and none is named after the "
            "plugin — community packages ship exactly one server"
        )
    if not isinstance(server, Mapping):
        raise AgentPluginError(f"mcp.json server {server_name!r} must be an object")
    server_type = server.get("type")
    if server_type == "streamable-http":
        return _convert_http_server(str(server_name), server, extension)
    if server_type == "stdio":
        return _convert_stdio_server(str(server_name), server)
    if server_type == "sse":
        raise AgentPluginError(
            "mcp.json: the deprecated 'sse' transport is not accepted for "
            "community plugins — publish a streamable-http endpoint"
        )
    raise AgentPluginError(f"mcp.json: unknown server type {server_type!r}")


def convert_manifest(
    plugin_json: Mapping[str, Any],
    mcp_json: Mapping[str, Any] | None = None,
    *,
    publisher: str | None = None,
    version: str | None = None,
    source_url: str | None = None,
) -> PluginSpec:
    """Validate an Agent Plugins v1.0.0 manifest set and build a `PluginSpec`.

    ``publisher``/``version``/``source_url`` come from the registry index
    entry, not from the manifest, so a manifest cannot claim someone else's
    identity.
    """
    if not isinstance(plugin_json, Mapping):
        raise AgentPluginError("plugin.json must be a JSON object")
    name = validate_spec_name(plugin_json.get("name"))
    description = _require_str(plugin_json.get("description"), "plugin description")

    extensions = plugin_json.get("extensions")
    extension = extensions.get(EXTENSION_NAMESPACE) if isinstance(extensions, Mapping) else None
    if not isinstance(extension, Mapping):
        raise AgentPluginError(
            f'plugin.json needs extensions["{EXTENSION_NAMESPACE}"] with the '
            "Jarvis auth block — see docs/marketplace/agent-plugins-standard.md"
        )
    if extension.get("native_tool"):
        # Native tools are Python code inside the app package. A manifest
        # cannot ship code, so a community entry claiming one would install
        # as a dead card at best and shadow a real tool at worst.
        raise AgentPluginError(
            "community plugins cannot bind native_tool — that tier is "
            "repo-contributed (see public-marketplace-analysis.md §2)"
        )
    auth = extension.get("auth")
    if not isinstance(auth, Mapping):
        raise AgentPluginError(
            f'extensions["{EXTENSION_NAMESPACE}"].auth is required (one of the '
            "five catalog auth modes)"
        )
    _reject_http_urls(auth, "auth")

    mcp_server = _convert_mcp_json(name, mcp_json, extension) if mcp_json is not None else None

    logo_url = extension.get("logo_url")
    if logo_url is not None:
        logo_url = _require_str(logo_url, "logo_url")
        _reject_http_urls(logo_url, "logo_url")

    try:
        return PluginSpec.model_validate(
            {
                "id": name,
                "display_name": extension.get("display_name") or name.replace("-", " ").title(),
                "description": description,
                "category": extension.get("category") or "Community",
                "logo_slug": extension.get("logo_slug") or name,
                "logo_color": extension.get("logo_color"),
                "logo_url": logo_url,
                # Community entries never self-promote into the featured row.
                "featured": False,
                "longevity": extension.get("longevity", "self_renewing"),
                "longevity_note": extension.get("longevity_note"),
                "oauth_client_family": extension.get("oauth_client_family"),
                "auth": dict(auth),
                "mcp_server": mcp_server,
                "post_install_hint_md": extension.get("post_install_hint_md"),
                "source": "community",
                "publisher": publisher,
                "version": version,
                "source_url": source_url,
            }
        )
    except ValidationError as exc:
        errors = exc.errors()
        location, message = "manifest", "invalid value"
        if errors:
            location = ".".join(str(part) for part in errors[0]["loc"]) or location
            message = str(errors[0]["msg"])
        raise AgentPluginError(f"manifest rejected at {location}: {message}") from exc


__all__ = ["AgentPluginError", "EXTENSION_NAMESPACE", "convert_manifest", "validate_spec_name"]
