"""The marketplace catalog must ship a tracked package seed.

`data/plugin_catalog.json` is gitignored runtime state — a fresh clone or a
headless VPS has no such file. Without a tracked seed the marketplace would be
empty there (a cloud-first violation). So `load_catalog` reads the user-editable
`data/` override when present, else the tracked `seed_catalog.json` shipped in
the package.
"""
from __future__ import annotations

import json

from jarvis.marketplace import catalog_data
from jarvis.marketplace.catalog_data import clear_cache, load_catalog


def test_package_seed_exists_and_is_valid() -> None:
    clear_cache()
    cat = load_catalog(catalog_data._PACKAGE_SEED_PATH)
    ids = {p.id for p in cat.plugins}
    assert {"github", "notion", "linear"} <= ids


def test_falls_back_to_seed_when_no_data_override(monkeypatch, tmp_path) -> None:
    clear_cache()
    monkeypatch.setattr(catalog_data, "_DEFAULT_CATALOG_PATH", tmp_path / "absent.json")
    cat = load_catalog()
    ids = {p.id for p in cat.plugins}
    assert "linear" in ids, "fresh install must get connectors from the package seed"
    clear_cache()


def test_data_override_wins_when_present(monkeypatch, tmp_path) -> None:
    clear_cache()
    override = tmp_path / "plugin_catalog.json"
    override.write_text(
        json.dumps({"version": 9, "schema_version": "ovr", "plugins": []}),
        encoding="utf-8",
    )
    monkeypatch.setattr(catalog_data, "_DEFAULT_CATALOG_PATH", override)
    cat = load_catalog()
    assert cat.version == 9
    assert cat.plugins == []
    clear_cache()
