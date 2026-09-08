"""Check every owned asset and the shared diorama building/navigation contract."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/world"))
from asset_inventory import ASSETS, INVENTORY, inventory, validate_building  # noqa: E402


def check() -> list[str]:
    manifest = json.loads((ASSETS / "world/world-manifest.json").read_text(encoding="utf-8"))
    problems = []
    if manifest.get("contract") != 2:
        problems.append("world contract must be v2")
    if manifest["navigation"] != {"cellM": 0.5, "sectorM": 32, "tickHz": 30, "maxStepM": 0.25}:
        problems.append("navigation contract drift")
    for key, asset in manifest["buildings"].items():
        path = ASSETS / "world/kit" / f"{asset['asset']}.glb"
        if not path.is_file():
            problems.append(f"{key}: missing GLB")
            continue
        problems.extend(f"{key}: {p}" for p in validate_building(path, asset))
    declared = {f"{a['asset']}.glb" for a in manifest["buildings"].values()}
    actual = {p.name for p in (ASSETS / "world/kit").glob("*.glb")}
    if actual != declared:
        problems.append("building manifest does not cover the complete kit")
    if not INVENTORY.is_file():
        problems.append("missing generated asset inventory")
    elif json.loads(INVENTORY.read_text(encoding="utf-8")) != inventory():
        problems.append("asset inventory is stale; rebuild it after exporting assets")
    return problems


def main() -> int:
    problems = check()
    for problem in problems:
        print(f"[world-kit] {problem}")
    if not problems:
        print("[world-kit] complete asset inventory and collision contract OK")
    return bool(problems)


if __name__ == "__main__":
    raise SystemExit(main())
