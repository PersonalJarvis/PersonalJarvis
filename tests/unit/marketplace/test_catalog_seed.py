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


def _seed():
    """Load the tracked package seed directly (independent of any data/ override)."""
    clear_cache()
    return load_catalog(catalog_data._PACKAGE_SEED_PATH)


def test_stripe_is_pat_paste_with_stdio_mcp() -> None:
    # Stripe's hosted-MCP DCR OAuth bounced real users to the dashboard (no
    # consent); switched to the official restricted-key path which connects
    # reliably.
    spec = _seed().by_id("stripe")
    assert spec is not None
    assert spec.display_name == "Stripe"  # must match the COMING_SOON label
    assert spec.auth.mode == "pat_paste"
    assert spec.auth.validation_endpoint == "https://api.stripe.com/v1/balance"
    assert spec.mcp_server is not None
    # Restricted key used as a Bearer token against the hosted MCP (bypasses
    # the OAuth client-allowlist, Node-free) — not the deprecated --tools stdio.
    assert spec.mcp_server["transport"] == "http"
    assert spec.mcp_server["url"] == "https://mcp.stripe.com"


def test_cloudflare_is_dcr_one_click_with_http_mcp() -> None:
    spec = _seed().by_id("cloudflare")
    assert spec is not None
    assert spec.display_name == "Cloudflare"
    assert spec.auth.mode == "hosted_mcp_oauth_dcr"
    assert spec.auth.mcp_url == "https://observability.mcp.cloudflare.com/mcp"
    assert spec.mcp_server["url"] == "https://observability.mcp.cloudflare.com/mcp"


def test_discord_is_bot_pat_with_stdio_mcp() -> None:
    spec = _seed().by_id("discord")
    assert spec is not None
    assert spec.display_name == "Discord"
    assert spec.auth.mode == "pat_paste"
    assert spec.auth.auth_scheme == "bot"
    assert spec.mcp_server["transport"] == "stdio"
    assert "mcp-discord" in spec.mcp_server["install"]


def test_telegram_is_pat_telegram_path_no_mcp() -> None:
    spec = _seed().by_id("telegram")
    assert spec is not None
    assert spec.display_name == "Telegram"
    assert spec.auth.mode == "pat_paste"
    assert spec.auth.auth_scheme == "telegram_path"
    assert "{token}" in spec.auth.validation_endpoint
    # Telegram reuses the in-repo channel, not an MCP server.
    assert spec.mcp_server is None


def test_asana_is_pkce_loopback_with_resource_and_http_mcp() -> None:
    spec = _seed().by_id("asana")
    assert spec is not None
    assert spec.display_name == "Asana"
    assert spec.auth.mode == "oauth_pkce_loopback"
    assert spec.auth.resource == "https://mcp.asana.com/v2"
    assert spec.mcp_server["url"] == "https://mcp.asana.com/v2/mcp"


def test_google_drive_uses_drive_file_scope() -> None:
    spec = _seed().by_id("google_drive")
    assert spec is not None
    assert spec.display_name == "Google Drive"
    assert spec.auth.mode == "oauth_pkce_loopback"
    assert "https://www.googleapis.com/auth/drive.file" in spec.auth.scopes
    assert spec.auth.scope_separator == "space"
    assert spec.auth.callback_path == ""
    assert spec.auth.offline_access is True


def test_gmail_pkce_loopback_read_and_send_scopes() -> None:
    spec = _seed().by_id("gmail")
    assert spec is not None
    assert spec.display_name == "Gmail"
    assert spec.auth.mode == "oauth_pkce_loopback"
    assert "https://www.googleapis.com/auth/gmail.readonly" in spec.auth.scopes
    assert "https://www.googleapis.com/auth/gmail.send" in spec.auth.scopes
    assert spec.auth.scope_separator == "space"
    assert spec.auth.callback_path == ""
    assert spec.auth.offline_access is True
    assert spec.native_tool == "gmail"
