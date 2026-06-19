"""Anti-drift guard: every PLUGIN_CLI_OVERLAP entry maps a real plugin id to a
real CLI name. Prevents the map rotting when a catalog entry is renamed
(multi-layer drift class — docs/anti-drift-three-layer.md)."""
import json
from pathlib import Path

from jarvis.clis.capability_provider import PLUGIN_CLI_OVERLAP

_ROOT = Path(__file__).resolve().parents[3]


def _plugin_ids() -> set[str]:
    data = json.loads(
        (_ROOT / "jarvis/marketplace/seed_catalog.json").read_text(encoding="utf-8")
    )
    return {p["id"] for p in data.get("plugins", [])}


def _cli_names() -> set[str]:
    data = json.loads(
        (_ROOT / "jarvis/clis/catalog/seed_catalog.json").read_text(encoding="utf-8")
    )
    items = data if isinstance(data, list) else data.get("clis", data.get("entries", []))
    return {c["name"] for c in items if isinstance(c, dict)}


def test_overlap_keys_are_real_plugin_ids():
    unknown = set(PLUGIN_CLI_OVERLAP) - _plugin_ids()
    assert not unknown, f"PLUGIN_CLI_OVERLAP keys not in plugin catalog: {unknown}"


def test_overlap_values_are_real_cli_names():
    unknown = set(PLUGIN_CLI_OVERLAP.values()) - _cli_names()
    assert not unknown, f"PLUGIN_CLI_OVERLAP values not in CLI catalog: {unknown}"
