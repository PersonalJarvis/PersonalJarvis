"""Loader for `data/plugin_catalog.json`."""

from __future__ import annotations

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
            "env_template": {
                "GITHUB_PERSONAL_ACCESS_TOKEN": "$plugin_github_access_token"
            },
        },
        {
            "transport": "http",
            "url": "https://api.githubcopilot.com/mcp/",
            "auth_header_template": (
                "Authorization: Bearer $plugin_github_access_token"
            ),
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
            "auth_header_template": (
                "Authorization: Bearer $plugin_supabase_access_token"
            ),
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
