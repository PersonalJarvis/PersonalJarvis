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


def _read(path: Path) -> PluginCatalog:
    with path.open(encoding="utf-8") as f:
        raw = json.load(f)
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
