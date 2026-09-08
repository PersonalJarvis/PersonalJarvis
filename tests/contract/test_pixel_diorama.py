"""Owned diorama assets are portable and retain saved recipe compatibility."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/world"))
from asset_inventory import ASSETS, INVENTORY, inventory, validate_building  # noqa: E402


def test_every_owned_asset_has_current_inventory() -> None:
    recorded = json.loads(INVENTORY.read_text(encoding="utf-8"))
    assert recorded == inventory()
    assert len(recorded["assets"]) >= 95
    assert {row["kind"] for row in recorded["assets"]} == {"building", "figure", "part", "clips"}
    assert all(not row["file"].startswith(("/", "..")) for row in recorded["assets"])


def test_buildings_export_the_runtime_collision_contract() -> None:
    manifest = json.loads((ASSETS / "world/world-manifest.json").read_text(encoding="utf-8"))
    for spec in manifest["buildings"].values():
        assert validate_building(ASSETS / "world/kit" / f"{spec['asset']}.glb", spec) == []


def test_collision_contract_rejects_drift() -> None:
    manifest = json.loads((ASSETS / "world/world-manifest.json").read_text(encoding="utf-8"))
    changed = {**manifest["buildings"]["plugins"], "stand": [0, 100]}
    assert "manifest mismatch: stand" in validate_building(
        ASSETS / "world/kit/plugin-docks.glb", changed
    )


def test_catalog_keeps_all_recipe_families_and_resolvable_parts() -> None:
    catalog = json.loads(
        (ROOT / "jarvis/ui/web/frontend/src/components/society/figures/catalog.json").read_text(
            encoding="utf-8"
        )
    )
    assert catalog["contract"] == 1  # persisted recipes do not need rewriting
    assert {b["archetype"] for b in catalog["bases"]} == {"biped", "quadruped", "spirit"}
    for row in catalog["bases"] + catalog["parts"]:
        assert (ASSETS / "figures" / row["file"]).is_file()


def test_headless_validator_imports_without_blender() -> None:
    spec = importlib.util.spec_from_file_location(
        "diorama_contract_gate", ROOT / "scripts/ci/check_world_kit.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.check() == []
