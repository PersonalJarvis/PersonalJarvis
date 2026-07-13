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


def _read(path: Path) -> PluginCatalog:
    with path.open(encoding="utf-8-sig") as f:
        raw = json.load(f)
    if path.resolve() == _DEFAULT_CATALOG_PATH.resolve():
        raw = _migrate_obsolete_mcp_transports(raw)
    return PluginCatalog.model_validate(raw)


def _resolve_path(path: Path | None) -> Path:
    """Explicit path wins; else the user's data/ override; else the seed."""
    if path is not None:
        return path
    if _DEFAULT_CATALOG_PATH.exists():
        return _DEFAULT_CATALOG_PATH
    return _PACKAGE_SEED_PATH


@lru_cache(maxsize=4)
def load_catalog(path: Path | None = None) -> PluginCatalog:
    return _read(_resolve_path(path))


def clear_cache() -> None:
    load_catalog.cache_clear()
