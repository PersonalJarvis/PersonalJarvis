"""Persist community plugin installs into the user's ``data/`` catalog.

"Install" was always designed to be a file write (public-marketplace-analysis
§1): the catalog loader keeps override entries the seed does not know, so a
community plugin becomes a first-class store card by appending its converted
`PluginSpec` to ``data/plugin_catalog.json``. Uninstall removes exactly that
entry — seed plugins are structurally out of reach here because the loader
re-adds them from the package on every merge.

Writes are atomic (tmp + replace) and go through one place so the
read-modify-write cycle stays on the event loop thread without interleaved
awaits — the same discipline `config_writer` applies to jarvis.toml.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from typing import Any

from jarvis.marketplace import catalog_data
from jarvis.marketplace.catalog import PluginSpec

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def seed_plugin_ids() -> frozenset[str]:
    """Ids shipped in the package seed — reserved against community installs."""
    try:
        raw = json.loads(catalog_data._PACKAGE_SEED_PATH.read_text(encoding="utf-8-sig"))
        return frozenset(
            str(plugin["id"])
            for plugin in raw.get("plugins", [])
            if isinstance(plugin, dict) and plugin.get("id")
        )
    except (OSError, ValueError):
        # An unreadable seed already degrades catalog loading; the guard then
        # fails open and the loader's own validation still applies.
        logger.warning("seed catalog unreadable — reserved-id guard is empty")
        return frozenset()


def _read_override() -> dict[str, Any]:
    """The user's override document, or a fresh skeleton matching the seed."""
    try:
        raw = json.loads(catalog_data._DEFAULT_CATALOG_PATH.read_text(encoding="utf-8-sig"))
        if isinstance(raw, dict) and isinstance(raw.get("plugins"), list):
            return raw
        logger.warning("plugin catalog override has no plugins list — rebuilding")
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as exc:
        # Never silently discard a user's catalog: an unreadable override is
        # surfaced to the caller instead of being overwritten with a skeleton.
        raise RuntimeError(
            f"data/plugin_catalog.json exists but cannot be parsed ({exc}); "
            "fix or remove it before installing community plugins"
        ) from exc
    version, schema_version = 1, "2026-05-09"
    try:
        seed = json.loads(catalog_data._PACKAGE_SEED_PATH.read_text(encoding="utf-8-sig"))
        version = int(seed.get("version", version))
        schema_version = str(seed.get("schema_version", schema_version))
    except (OSError, ValueError):
        logger.warning("seed catalog unreadable — using skeleton catalog header")
    return {"version": version, "schema_version": schema_version, "plugins": []}


def _write_override(document: dict[str, Any]) -> None:
    catalog_data._DEFAULT_CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = catalog_data._DEFAULT_CATALOG_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(catalog_data._DEFAULT_CATALOG_PATH)
    catalog_data.clear_cache()


def install_plugin_spec(spec: PluginSpec) -> None:
    """Add (or update) a community entry in the override catalog."""
    document = _read_override()
    document["plugins"] = [
        plugin
        for plugin in document["plugins"]
        if not (isinstance(plugin, dict) and plugin.get("id") == spec.id)
    ]
    document["plugins"].append(spec.model_dump(mode="json"))
    _write_override(document)


def remove_community_plugin(plugin_id: str) -> bool:
    """Drop a community entry from the override. Returns True when removed."""
    document = _read_override()
    before = len(document["plugins"])
    document["plugins"] = [
        plugin
        for plugin in document["plugins"]
        if not (isinstance(plugin, dict) and plugin.get("id") == plugin_id)
    ]
    if len(document["plugins"]) == before:
        return False
    _write_override(document)
    return True


__all__ = ["install_plugin_spec", "remove_community_plugin", "seed_plugin_ids"]
