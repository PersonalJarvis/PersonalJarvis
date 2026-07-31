"""The roster as a FILTER: what discovery finds vs. what UltraWiki offers.

Discovery reads the user's registries and will happily report a payment
processor, a deployment platform, and a hand-named MCP server. The picker must
show none of those, must never render a registry's own display string, and must
still show a curated integration the user has not connected — flagged, so it
sends them to the Plugins store instead of registering a dead source.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.ultrawiki import connector_catalog as catalog
from jarvis.ultrawiki.connectors import plugin_bridge
from jarvis.ultrawiki.service import UltraWikiService, _connector_identity


def _plugin(plugin_id: str, display_name: str, category: str = "Developer"):
    return SimpleNamespace(
        id=plugin_id, display_name=display_name, category=category
    )


@pytest.fixture
def registries(monkeypatch: pytest.MonkeyPatch):
    """Drive both discovery probes from one place.

    ``connected`` holds the marketplace plugins with a live token; ``mcp`` holds
    the raw ``mcp.json`` server map. Both start empty and a test fills what it
    needs — the maintainer's own machine must never decide the outcome (§3).
    """
    import jarvis.marketplace.catalog_data as catalog_data
    import jarvis.marketplace.token_store as token_store
    import jarvis.mcp.state as mcp_state

    state = SimpleNamespace(plugins=[], connected=set(), mcp={})

    class FakeTokenStore:
        def load(self, plugin_id: str):
            if plugin_id in state.connected:
                return SimpleNamespace(needs_reauth=False)
            return None

    monkeypatch.setattr(
        catalog_data, "load_catalog", lambda: SimpleNamespace(plugins=state.plugins)
    )
    monkeypatch.setattr(token_store, "TokenStore", FakeTokenStore)
    monkeypatch.setattr(mcp_state, "load_config", lambda: {"mcpServers": state.mcp})
    return state


class TestRosterFiltersDiscovery:
    def test_an_uncurated_plugin_never_reaches_the_picker(self, registries):
        registries.plugins = [
            _plugin("github", "GitHub"),
            _plugin("stripe", "Stripe"),
            _plugin("vercel", "Vercel"),
        ]
        registries.connected = {"github", "stripe", "vercel"}

        ids = [row["id"] for row in plugin_bridge.list_candidates()]
        assert ids == ["plugin:github"]

    def test_an_uncurated_mcp_server_never_reaches_the_picker(self, registries):
        registries.mcp = {
            "grace-plug-in-mcp": {"enabled": True},
            "notion": {"enabled": True},
        }

        rows = plugin_bridge.list_candidates()
        assert [row["id"] for row in rows] == ["mcp:notion"]

    def test_an_unconnected_plugin_is_not_reported_as_connected(self, registries):
        registries.plugins = [_plugin("github", "GitHub")]
        registries.connected = set()
        assert plugin_bridge.list_candidates() == []

    def test_one_product_connected_twice_yields_one_card(self, registries):
        """A plugin AND a hand-wired MCP server for the same product."""
        registries.plugins = [_plugin("notion", "Notion")]
        registries.connected = {"notion"}
        registries.mcp = {"notion": {"enabled": True}}

        rows = plugin_bridge.list_candidates()
        assert [row["id"] for row in rows] == ["plugin:notion"]

    def test_broken_registries_shorten_the_list_instead_of_raising(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        import jarvis.marketplace.catalog_data as catalog_data
        import jarvis.mcp.state as mcp_state

        def _boom(*_args, **_kwargs):
            raise RuntimeError("registry down")

        monkeypatch.setattr(catalog_data, "load_catalog", _boom)
        monkeypatch.setattr(mcp_state, "load_config", _boom)
        assert plugin_bridge.list_candidates() == []
        # The roster half still answers: what EXISTS does not depend on what
        # this machine has plugged in.
        offered = plugin_bridge.list_offered_integrations()
        assert len(offered) == len(catalog.BRIDGE_CONNECTORS)
        assert all(row["connected"] is False for row in offered)


class TestCandidateEnrichment:
    def test_the_catalog_label_wins_over_the_registry_string(self, registries):
        """Registry display strings drift; the product name does not."""
        registries.plugins = [_plugin("notion", "notion (beta) - grace plug in")]
        registries.connected = {"notion"}

        row = plugin_bridge.list_candidates()[0]
        assert row["label"] == "Notion"

    def test_an_mcp_server_gets_the_product_name_not_its_config_name(
        self, registries
    ):
        registries.mcp = {"google-drive": {"enabled": True, "display": "gdrive srv"}}

        row = plugin_bridge.list_candidates()[0]
        assert row["label"] == "Google Drive"
        assert row["brand"] == "google_drive"
        assert row["catalog_id"] == "google_drive"

    def test_every_candidate_carries_the_fields_a_tile_needs(self, registries):
        registries.plugins = [_plugin("slack", "Slack", "Messaging")]
        registries.connected = {"slack"}

        row = plugin_bridge.list_candidates()[0]
        assert row["brand"] == "slack"
        assert row["connector_kind"] == "bridge"
        assert row["description_key"] == "ultrawiki.connectors.slack"
        assert row["connected"] is True
        # Discovery origin is preserved: it is WHERE it was found, not WHAT.
        assert row["kind"] == "plugin"

    def test_status_is_the_runtime_truth_not_the_catalog_claim(self, registries):
        """A registered pull adapter means it really can be read today."""
        registries.plugins = [_plugin("github", "GitHub")]
        registries.connected = {"github"}
        assert plugin_bridge.list_candidates()[0]["status"] == "adapter_pending"

        async def _adapter(_ctx, _checkpoint):  # pragma: no cover - never run
            return
            yield

        plugin_bridge.register_pull_adapter("plugin:github", _adapter)
        try:
            row = plugin_bridge.list_candidates()[0]
            assert row["status"] == "available"
            assert row["has_pull_adapter"] is True
        finally:
            plugin_bridge.unregister_pull_adapter("plugin:github")

    def test_candidates_keep_roster_order_not_discovery_order(self, registries):
        registries.plugins = [
            _plugin("telegram", "Telegram"),
            _plugin("github", "GitHub"),
            _plugin("notion", "Notion"),
        ]
        registries.connected = {"telegram", "github", "notion"}

        order = [row["catalog_id"] for row in plugin_bridge.list_candidates()]
        assert order == ["github", "notion", "telegram"]


class TestOfferedIntegrations:
    def test_the_picker_sees_curated_entries_it_has_not_connected(
        self, registries
    ):
        registries.plugins = [_plugin("github", "GitHub")]
        registries.connected = {"github"}

        rows = plugin_bridge.list_offered_integrations()
        assert len(rows) == len(catalog.BRIDGE_CONNECTORS)
        by_id = {row["catalog_id"]: row for row in rows}
        assert by_id["github"]["connected"] is True
        assert by_id["notion"]["connected"] is False
        # Not connected means not readable — never quietly "available".
        assert by_id["notion"]["has_pull_adapter"] is False
        assert by_id["notion"]["brand"] == "notion"

    def test_connected_entries_come_first(self, registries):
        registries.plugins = [_plugin("telegram", "Telegram")]
        registries.connected = {"telegram"}

        rows = plugin_bridge.list_offered_integrations()
        assert rows[0]["catalog_id"] == "telegram"
        assert all(not row["connected"] for row in rows[1:])

    def test_nothing_outside_the_roster_can_appear(self, registries):
        registries.plugins = [_plugin("stripe", "Stripe")]
        registries.connected = {"stripe"}
        registries.mcp = {"my-secret-server": {"enabled": True}}

        offered = {row["catalog_id"] for row in plugin_bridge.list_offered_integrations()}
        assert offered == {spec.id for spec in catalog.BRIDGE_CONNECTORS}


class TestSourceIdentity:
    def test_labels_resolve_through_the_catalog_first(self):
        """No registry probe needed: the roster already knows the name."""
        resolved = UltraWikiService._resolve_label(
            "plugin-bridge",
            "Connected integration (plugins / MCP)",
            {"integration_id": "plugin:github"},
        )
        assert resolved == "GitHub"

    def test_a_user_chosen_label_is_never_overwritten(self):
        resolved = UltraWikiService._resolve_label(
            "plugin-bridge", "Work repos", {"integration_id": "plugin:github"}
        )
        assert resolved == "Work repos"

    def test_an_uncurated_integration_keeps_the_callers_label(self):
        resolved = UltraWikiService._resolve_label(
            "plugin-bridge",
            "connected integration",
            {"integration_id": "mcp:something-private"},
        )
        assert resolved == "connected integration"

    def test_brand_and_kind_are_derived_for_builtins_and_bridges(self):
        assert _connector_identity("local-folder", "") == ("folder", "builtin")
        assert _connector_identity("obsidian-vault", "") == ("vault", "builtin")
        assert _connector_identity("export-import", "") == ("archive", "builtin")
        assert _connector_identity("plugin-bridge", "plugin:slack") == (
            "slack",
            "bridge",
        )

    def test_an_unknown_connector_answers_honestly_instead_of_guessing(self):
        """Empty means "not one of ours" — the UI then draws a monogram."""
        assert _connector_identity("some-third-party", "") == ("", "")
        assert _connector_identity("plugin-bridge", "mcp:private") == ("", "bridge")

    def test_source_summaries_carry_the_brand_fields(self):
        summary = UltraWikiService._source_summary(
            {
                "id": "plugin-bridge-abc123",
                "connector": "plugin-bridge",
                "label": "GitHub",
                "config": {"integration_id": "plugin:github"},
                "consent": "approved",
                "enabled": True,
                "areas": [],
                "counts": None,
                "sync_state": None,
                "last_sync_at": None,
                "last_error": None,
                "last_notice": None,
            }
        )
        assert summary["brand"] == "github"
        assert summary["connector_kind"] == "bridge"
        assert summary["integration_id"] == "plugin:github"
