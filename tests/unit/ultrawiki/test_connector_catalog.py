"""The curated connector roster: shape, provenance, and its cross-layer twins.

The roster is a PRODUCT surface — every entry becomes a tile with a logo and a
real name — so the things that can silently rot are pinned here:

* an entry naming a plugin the marketplace does not ship (unconnectable),
* an entry whose brand mark is not bundled (a monogram where a logo belongs),
* a description key no locale defines (the raw key rendered as UI text),
* and the Python -> TypeScript value lists drifting apart (AP-4 / BUG-008).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import jarvis
from jarvis.ultrawiki import connector_catalog as catalog
from jarvis.ultrawiki.connectors import builtin_connectors

PACKAGE_ROOT = Path(jarvis.__file__).resolve().parent
FRONTEND_SRC = PACKAGE_ROOT / "ui" / "web" / "frontend" / "src"
BRAND_DIR = FRONTEND_SRC / "assets" / "brands"
LOCALE_DIR = FRONTEND_SRC / "i18n" / "locales"
API_TS = FRONTEND_SRC / "lib" / "ultrawikiApi.ts"

#: The bridge connector is plumbing, not a product — it must never be a card.
BRIDGE_CONNECTOR_ID = "plugin-bridge"


def _locale(name: str) -> dict:
    return json.loads((LOCALE_DIR / f"{name}.json").read_text(encoding="utf-8"))


def _resolve(locale: dict, dotted_key: str) -> object:
    node: object = locale
    for part in dotted_key.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _ts_string_array(name: str) -> list[str]:
    """Read a `const <name> = [...] as const` array out of the TS module."""
    source = API_TS.read_text(encoding="utf-8")
    match = re.search(rf"const {name} = \[(.*?)\] as const", source, re.DOTALL)
    assert match is not None, f"{name} not found in {API_TS.name}"
    return re.findall(r'"([^"]+)"', match.group(1))


class TestCatalogShape:
    def test_ids_are_unique_across_the_whole_roster(self):
        ids = [spec.id for spec in catalog.ALL_CONNECTORS]
        assert len(ids) == len(set(ids))

    def test_every_entry_is_fully_described(self):
        for spec in catalog.ALL_CONNECTORS:
            assert spec.label.strip(), f"{spec.id} has no label"
            assert spec.brand.strip(), f"{spec.id} has no brand"
            assert spec.kind in catalog.CONNECTOR_KINDS
            assert spec.status in catalog.CONNECTOR_STATUSES
            assert spec.description_key.startswith("ultrawiki.connectors.")

    def test_builtins_cover_every_shipped_connector_except_the_bridge(self):
        """A built-in connector missing here is unreachable from the picker."""
        shipped = set(builtin_connectors()) - {BRIDGE_CONNECTOR_ID}
        assert {spec.id for spec in catalog.BUILTIN_CONNECTORS} == shipped

    def test_the_export_on_ramp_is_offered_as_a_vendor_free_builtin(self):
        """It belongs to no vendor: the formats it reads are open standards,
        so it must carry a neutral glyph rather than borrow a service's mark."""
        spec = catalog.get_connector("export-import")
        assert spec is not None
        assert spec.kind == "builtin"
        assert spec.brand == "archive"
        assert spec.brand in catalog.NEUTRAL_BRANDS
        assert spec.status == "available"
        assert catalog.as_dict(spec)["connector"] == "export-import"

    def test_the_bridge_itself_is_not_offered_as_a_card(self):
        assert catalog.get_connector(BRIDGE_CONNECTOR_ID) is None
        assert not any(
            spec.id == BRIDGE_CONNECTOR_ID for spec in catalog.ALL_CONNECTORS
        )

    def test_builtins_use_a_neutral_icon_and_a_translatable_name(self):
        """A built-in has no vendor and must not borrow one's mark."""
        for spec in catalog.BUILTIN_CONNECTORS:
            assert spec.brand in catalog.NEUTRAL_BRANDS
            assert spec.label_key.startswith("ultrawiki.")

    def test_bridge_entries_declare_a_vendor_brand_and_no_label_key(self):
        """A product name is the vendor's own spelling — never translated."""
        for spec in catalog.BRIDGE_CONNECTORS:
            assert spec.brand not in catalog.NEUTRAL_BRANDS
            assert spec.label_key == ""

    def test_as_dict_routes_builtins_and_bridges_to_the_right_connector(self):
        for spec in catalog.BUILTIN_CONNECTORS:
            row = catalog.as_dict(spec)
            assert row["connector"] == spec.id
            assert row["integration_id"] == ""
        for spec in catalog.BRIDGE_CONNECTORS:
            row = catalog.as_dict(spec)
            assert row["connector"] == BRIDGE_CONNECTOR_ID
            assert row["integration_id"] == f"plugin:{spec.id}"


class TestRosterProvenance:
    """Every bridge entry names something that actually EXISTS."""

    def test_every_bridge_entry_is_a_plugin_the_marketplace_ships(self):
        from jarvis.marketplace.catalog_data import load_catalog

        shipped = {plugin.id for plugin in load_catalog().plugins}
        offered = {spec.id for spec in catalog.BRIDGE_CONNECTORS}
        assert offered <= shipped, (
            "UltraWiki offers integrations the marketplace cannot connect: "
            f"{sorted(offered - shipped)}"
        )

    def test_every_brand_key_has_a_bundled_mark(self):
        """A missing file is not a crash — it is a monogram where a logo
        belongs, which is exactly the regression this roster exists to end."""
        missing = [
            brand
            for brand in sorted(catalog.brand_asset_keys())
            if not (BRAND_DIR / f"{brand}.svg").is_file()
        ]
        assert missing == [], f"no bundled brand mark for: {missing}"


class TestDescriptionsAreTranslated:
    def test_every_description_key_resolves_in_every_locale(self):
        for locale_name in ("en", "de", "es"):
            locale = _locale(locale_name)
            for spec in catalog.ALL_CONNECTORS:
                text = _resolve(locale, spec.description_key)
                assert isinstance(text, str) and text.strip(), (
                    f"{locale_name} has no text for {spec.description_key}"
                )

    def test_builtin_label_keys_resolve_too(self):
        for locale_name in ("en", "de", "es"):
            locale = _locale(locale_name)
            for spec in catalog.BUILTIN_CONNECTORS:
                assert isinstance(_resolve(locale, spec.label_key), str)


class TestPythonTypeScriptParity:
    """Five-layer discipline (AP-4): the picker branches on these values."""

    def test_kinds_match(self):
        assert _ts_string_array("ULTRAWIKI_CONNECTOR_KINDS") == list(
            catalog.CONNECTOR_KINDS
        )

    def test_statuses_match(self):
        assert _ts_string_array("ULTRAWIKI_CONNECTOR_STATUSES") == list(
            catalog.CONNECTOR_STATUSES
        )

    def test_neutral_brands_match(self):
        assert _ts_string_array("ULTRAWIKI_NEUTRAL_BRANDS") == list(
            catalog.NEUTRAL_BRANDS
        )


class TestIntegrationIdMatching:
    def test_both_discovery_prefixes_reach_the_same_entry(self):
        """Where an integration was FOUND says nothing about what it is."""
        from_plugin = catalog.bridge_entry_for("plugin:github")
        from_mcp = catalog.bridge_entry_for("mcp:github")
        assert from_plugin is not None
        assert from_plugin is from_mcp

    def test_spelling_variants_fold_onto_one_entry(self):
        drive = catalog.get_connector("google_drive")
        for spelling in ("plugin:google_drive", "mcp:google-drive", "mcp:gdrive"):
            assert catalog.bridge_entry_for(spelling) is drive

    def test_an_uncurated_integration_is_not_offered(self):
        assert catalog.bridge_entry_for("mcp:some-private-server") is None
        assert catalog.bridge_entry_for("plugin:stripe") is None
        assert not catalog.is_offered("plugin:stripe")

    def test_empty_and_bare_ids_are_handled(self):
        assert catalog.bridge_entry_for("") is None
        assert catalog.bridge_entry_for("   ") is None
        # A bare product id (no prefix) still resolves — the prefix is metadata.
        assert catalog.bridge_entry_for("notion") is catalog.get_connector("notion")

    def test_a_builtin_id_is_never_a_bridge_integration(self):
        assert catalog.bridge_entry_for("plugin:local-folder") is None
