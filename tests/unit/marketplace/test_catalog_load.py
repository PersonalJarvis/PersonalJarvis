"""Smoke test for `data/plugin_catalog.json` against the Pydantic schema.

Catches drift like the BUG-016 case: a new top-level field landed in the
JSON without the matching `PluginSpec` field, the strict `extra="forbid"`
config rejected validation, the marketplace route returned 500, and the
PluginsView rendered "Backend unreachable" with zero hits.

This test re-reads the canonical catalog file from disk, asserts the
discriminated `auth` union resolves for every plugin, and asserts the
contract the frontend depends on (id, display_name, category, auth.mode).
"""

from __future__ import annotations

from jarvis.marketplace.catalog_data import clear_cache, load_catalog


def test_catalog_loads_without_validation_error() -> None:
    clear_cache()
    catalog = load_catalog()
    assert catalog.version >= 1
    assert catalog.schema_version
    assert len(catalog.plugins) > 0


def test_every_plugin_has_a_resolvable_auth_block() -> None:
    clear_cache()
    catalog = load_catalog()
    known_modes = {
        "pat_paste",
        "oauth_device_flow",
        "hosted_mcp_oauth_dcr",
        "oauth_pkce_loopback",
        "hosted_mcp_allowlist",
    }
    for plugin in catalog.plugins:
        assert plugin.id, "plugin without id"
        assert plugin.display_name, f"{plugin.id}: missing display_name"
        assert plugin.category in {"Developer", "Productivity", "Communication"}
        assert plugin.auth.mode in known_modes, (
            f"{plugin.id}: unknown auth mode {plugin.auth.mode!r}"
        )


def test_catalog_serializes_to_frontend_wire_shape() -> None:
    """The route returns `spec.model_dump(mode="json")`. Verify the shape
    actually matches what `PluginsView.adapt()` expects."""
    clear_cache()
    catalog = load_catalog()
    for plugin in catalog.plugins:
        wire = plugin.model_dump(mode="json")
        for required_key in ("id", "display_name", "description", "category", "logo_slug", "auth"):
            assert required_key in wire, f"{plugin.id}: wire shape missing {required_key}"
        assert "mode" in wire["auth"], f"{plugin.id}: auth block missing 'mode'"
