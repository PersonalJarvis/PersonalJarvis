"""Loader for `data/plugin_catalog.json`."""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path

from jarvis.marketplace.catalog import PluginCatalog

# User-editable runtime override (lives under the gitignored data/). Wins when
# present so a user / the Marketplace UI can curate connectors locally.
_DEFAULT_CATALOG_PATH = (
    Path(__file__).resolve().parent.parent.parent / "data" / "plugin_catalog.json"
)
# Tracked package seed — the canonical default catalog. A fresh clone or a
# headless VPS has no data/ override, so without this the marketplace would be
# empty there (cloud-first violation). Mirrors jarvis/skills/catalog +
# jarvis/clis/catalog, which ship their seed in-package.
_PACKAGE_SEED_PATH = Path(__file__).parent / "seed_catalog.json"


_PORTABLE_MCP_MIGRATIONS: dict[str, tuple[dict[str, object], dict[str, object]]] = {
    "github": (
        {
            "transport": "stdio",
            "install": [
                "docker",
                "run",
                "-i",
                "--rm",
                "-e",
                "GITHUB_PERSONAL_ACCESS_TOKEN",
                "ghcr.io/github/github-mcp-server",
            ],
            "env_template": {"GITHUB_PERSONAL_ACCESS_TOKEN": "$plugin_github_access_token"},
        },
        {
            "transport": "http",
            "url": "https://api.githubcopilot.com/mcp/",
            "auth_header_template": ("Authorization: Bearer $plugin_github_access_token"),
        },
    ),
    "supabase": (
        {
            "transport": "stdio",
            "install": [
                "npx",
                "-y",
                "@supabase/mcp-server-supabase@latest",
                "--read-only",
                "--access-token",
                "$plugin_supabase_access_token",
            ],
            "env_template": {},
        },
        {
            "transport": "http",
            "url": "https://mcp.supabase.com/mcp?read_only=true",
            "auth_header_template": ("Authorization: Bearer $plugin_supabase_access_token"),
        },
    ),
}

# Exact shipped discovery URLs that pointed at authorization-server metadata
# instead of the RFC 9728 protected-resource document. Existing installations
# may have copied these entries into data/plugin_catalog.json, whose auth block
# is intentionally user-owned. Upgrade only the known bad built-in value so a
# genuinely custom endpoint remains untouched.
_OAUTH_DISCOVERY_MIGRATIONS: dict[str, tuple[str, str]] = {
    "clickup": (
        "https://mcp.clickup.com/.well-known/oauth-authorization-server",
        "https://mcp.clickup.com/.well-known/oauth-protected-resource",
    ),
    "canva": (
        "https://mcp.canva.com/.well-known/oauth-authorization-server",
        "https://mcp.canva.com/.well-known/oauth-protected-resource",
    ),
    "airtable": (
        "https://airtable.com/.well-known/oauth-authorization-server/oauth2/v1",
        "https://mcp.airtable.com/.well-known/oauth-protected-resource",
    ),
    "cal_com": (
        "https://mcp.cal.com/.well-known/oauth-authorization-server",
        "https://mcp.cal.com/.well-known/oauth-protected-resource",
    ),
}


def _migrate_obsolete_mcp_transports(raw: object) -> object:
    """Upgrade exact built-in launcher specs without changing user variants.

    Older installs materialized the package catalog under ``data/``. That
    override otherwise wins forever and keeps GitHub dependent on Docker and
    Supabase dependent on Node.js even after the package seed is fixed. Only
    byte-equivalent legacy specs are upgraded in memory; any customized server
    command or endpoint remains untouched.
    """
    if not isinstance(raw, dict) or not isinstance(raw.get("plugins"), list):
        return raw
    for plugin in raw["plugins"]:
        if not isinstance(plugin, dict):
            continue
        migration = _PORTABLE_MCP_MIGRATIONS.get(str(plugin.get("id", "")))
        if migration is None:
            continue
        legacy, portable = migration
        if plugin.get("mcp_server") == legacy:
            plugin["mcp_server"] = portable
    return raw


def _migrate_obsolete_oauth_discovery(raw: object) -> object:
    """Upgrade exact broken built-in discovery URLs in a local override."""
    if not isinstance(raw, dict) or not isinstance(raw.get("plugins"), list):
        return raw
    for plugin in raw["plugins"]:
        if not isinstance(plugin, dict):
            continue
        migration = _OAUTH_DISCOVERY_MIGRATIONS.get(str(plugin.get("id", "")))
        auth = plugin.get("auth")
        if migration is None or not isinstance(auth, dict):
            continue
        obsolete, current = migration
        if auth.get("discovery_url") == obsolete:
            auth["discovery_url"] = current
    return raw


# What a user's `data/` override is allowed to keep when the package seed also
# knows the plugin: the connection details they may genuinely have customized
# (their own OAuth client id, a self-hosted MCP endpoint). Everything else —
# name, description, category, logo, longevity badge — is a PRODUCT decision
# that ships with the app, so it comes from the seed. Without this split, a
# taxonomy or logo change would land only on fresh installs.
_OVERRIDE_OWNED_FIELDS: tuple[str, ...] = ("auth", "mcp_server")

# Fingerprints of the complete PAT auth blocks shipped before browser login.
# Exact matching (including help text and endpoints) deliberately refuses to
# reinterpret custom credentials or self-hosted configurations as package data.
_LEGACY_PUBLISHER_AUTH_DIGESTS = {
    "gitlab": "149fcc1d805b63c414ce32b6ef5202efb5b226795ad1bf060609cc526b3d6774",
    "discord": "8d4e240eb5cc0865f7b074963b779baa7343a682b5e571439c244938af0944ab",
    "asana": "1ceac3beb408092f714ad5937bf5552a959ad6de47ed6bc597c9301175a3c82d",
    "linkedin": "29459bda8b9bbda691c7b2df06163a0716ec982c74ff629dad67ad1e27ffc6e1",
    "hubspot": "b1f7529f4a661e295b91dce5232994ca6e20e623497d27154e01f847a441e22e",
    "figma": "4eb9e6181f85d5534002126a77dbcfd7d95d28f41449fb140d31164b394a7051",
}

_LEGACY_PAT_AUTH_DIGESTS = {
    "home_assistant": "9167ac7a5c4d39b87e2a863cb2c71e2e4b4541b24b83626bbe86d393fa99d4b2",
    "github": "f7111c4bba8d16205ac3b2ff41f255b0cee0f3a3c86e4138e9b56b60054465fc",
    "vercel": "f7588c19421659a8cd22765c3015e76c96b59194b68c26b24a8346590f9bfe04",
    "supabase": "6ef753e23618090c7ff4e388d86a1fe91e5a4c78ce5a522963a248c3d4ee19d6",
    "stripe": "e19610b1b59a59ffe1e231e8d5adda082437eebb59cec1c2df215470c742dde4",
}
_LEGACY_PUBLISHER_AUTH_DIGESTS.update(
    {
        "slack": "48825f1d1cbee11f6d8d45e524325cbea053b26fb74a253267ec9d8c590aec15",
        "outlook": "a8d1e271f90dada555bc4b13ae570ed1471017dfc1419c9022efbc93156ac63f",
        "onedrive": "f729dd992e9ff96ae1dc46e94074f7ae83349f80abe8f86392bc62d61821f3b5",
        "teams": "14d10d34775fca3e3e40b1691362c830c7c3b79098594488dff70bff9c6e8c3e",
        "sharepoint": "05d0fb139cf7b967c9af09cc01220d8651aa278730c267e01dced8f8e11f726a",
        "onenote": "512430074094f49c7ed68434ffef03f0ce0c0bb272f0b3be96db03e1973e7fca",
        "microsoft_todo": "a739c5fa96e19c60f2ba8ac10fe30f04e58c7b1869a7f672ccb89db4a6b4bb5c",
        "azure": "08e4c7b3de36083920c536b0901175d95e8f1de22725546a7062b2690165d27a",
    }
)


# Full shipped Slack auth block before channel listing and Canvas permissions.
# Never widen a user's custom scopes, client, redirect, or server configuration.
_LEGACY_SLACK_AUTH_DIGEST = "7222ece276d51c75bcdf6f8f0dc28412674950ef21301975b7870a73340c1de2"


def _has_obsolete_slack_scopes(plugin: dict, seed_plugin: dict) -> bool:
    auth = plugin.get("auth")
    if plugin.get("id") != "slack" or not isinstance(auth, dict):
        return False
    digest = hashlib.sha256(
        json.dumps(auth, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return digest == _LEGACY_SLACK_AUTH_DIGEST and plugin.get("mcp_server") == seed_plugin.get(
        "mcp_server"
    )


def _has_obsolete_publisher_auth(plugin: dict, seed_plugin: dict) -> bool:
    auth = plugin.get("auth")
    if not isinstance(auth, dict):
        return False
    digest = hashlib.sha256(
        json.dumps(auth, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return digest == _LEGACY_PUBLISHER_AUTH_DIGESTS.get(plugin.get("id")) and plugin.get(
        "mcp_server"
    ) == seed_plugin.get("mcp_server")


def _has_obsolete_builtin_auth(plugin: dict, seed_plugin: dict) -> bool:
    auth = plugin.get("auth")
    if not isinstance(auth, dict) or auth.get("mode") != "pat_paste":
        return False
    plugin_id = str(plugin.get("id", ""))
    expected = _LEGACY_PAT_AUTH_DIGESTS.get(plugin_id)
    if expected is None:
        return False
    digest = hashlib.sha256(
        json.dumps(auth, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if digest != expected:
        return False
    # A user may have retained the original auth while choosing another MCP
    # server. Do not replace that pairing. Vercel previously had no MCP server.
    previous_mcp = None if plugin_id == "vercel" else seed_plugin.get("mcp_server")
    return plugin.get("mcp_server") == previous_mcp


def _merge_with_seed(override: object, seed: object) -> object:
    """Reconcile a user's `data/` catalog with the shipped package seed.

    Before this merge the override replaced the seed WHOLESALE, so a connector
    added to the shipped catalog reached only FRESH installs: every machine
    that had ever materialized ``data/`` kept serving its frozen list, and the
    new plugin was invisible there — while every test, which reads the seed
    directly, stayed green. That is the inverted form of "works on my machine",
    and it hid new catalog work from the very box it was developed on.

    Two rules now apply:
      * a seed plugin the override does not declare is ADDED;
      * a plugin both know is presented from the SEED, except for the fields in
        ``_OVERRIDE_OWNED_FIELDS``, which stay the user's.

    Consequence worth knowing: deleting an entry from the override no longer
    hides a plugin — the seed supplies it again. Curation means editing an
    entry, not removing it.
    """
    if not isinstance(override, dict) or not isinstance(override.get("plugins"), list):
        return override
    if not isinstance(seed, dict) or not isinstance(seed.get("plugins"), list):
        return override

    seed_by_id = {
        str(plugin["id"]): plugin
        for plugin in seed["plugins"]
        if isinstance(plugin, dict) and plugin.get("id")
    }
    merged_plugins: list[object] = []
    declared: set[str] = set()
    for plugin in override["plugins"]:
        if not isinstance(plugin, dict) or not plugin.get("id"):
            merged_plugins.append(plugin)
            continue
        plugin_id = str(plugin["id"])
        declared.add(plugin_id)
        seed_plugin = seed_by_id.get(plugin_id)
        if seed_plugin is None:
            merged_plugins.append(plugin)  # a purely local entry
            continue
        reconciled = dict(seed_plugin)
        if not (
            _has_obsolete_publisher_auth(plugin, seed_plugin)
            or _has_obsolete_builtin_auth(plugin, seed_plugin)
            or _has_obsolete_slack_scopes(plugin, seed_plugin)
        ):
            for field in _OVERRIDE_OWNED_FIELDS:
                if field in plugin:
                    reconciled[field] = plugin[field]
        merged_plugins.append(reconciled)

    merged_plugins.extend(
        plugin for plugin_id, plugin in seed_by_id.items() if plugin_id not in declared
    )
    result = dict(override)
    result["plugins"] = merged_plugins
    return result


def _read_raw(path: Path) -> object:
    with path.open(encoding="utf-8-sig") as f:
        return json.load(f)


def _read(path: Path) -> PluginCatalog:
    raw = _read_raw(path)
    if path.resolve() == _DEFAULT_CATALOG_PATH.resolve():
        raw = _migrate_obsolete_mcp_transports(raw)
        raw = _migrate_obsolete_oauth_discovery(raw)
    return PluginCatalog.model_validate(raw)


@lru_cache(maxsize=4)
def load_catalog(path: Path | None = None) -> PluginCatalog:
    """Explicit path wins; else the user's data/ override topped up from the
    package seed; else the seed alone (fresh clone / headless VPS)."""
    if path is not None:
        return _read(path)
    if not _DEFAULT_CATALOG_PATH.exists():
        return _read(_PACKAGE_SEED_PATH)
    raw = _migrate_obsolete_mcp_transports(_read_raw(_DEFAULT_CATALOG_PATH))
    raw = _migrate_obsolete_oauth_discovery(raw)
    try:
        seed_raw = _read_raw(_PACKAGE_SEED_PATH)
    except (OSError, ValueError):
        # A missing/corrupt package seed must never take the user's own
        # catalog down with it — serve the override unchanged.
        seed_raw = None
    if seed_raw is not None:
        raw = _merge_with_seed(raw, seed_raw)
    return PluginCatalog.model_validate(raw)


def clear_cache() -> None:
    load_catalog.cache_clear()
