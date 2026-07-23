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


def test_developer_connectors_use_hosted_mcp_without_local_runtimes() -> None:
    catalog = _seed()
    github = catalog.by_id("github")
    supabase = catalog.by_id("supabase")

    assert github is not None and github.mcp_server == {
        "transport": "http",
        "url": "https://api.githubcopilot.com/mcp/",
        "auth_header_template": (
            "Authorization: Bearer $plugin_github_access_token"
        ),
    }
    assert supabase is not None and supabase.mcp_server == {
        "transport": "http",
        "url": "https://mcp.supabase.com/mcp?read_only=true",
        "auth_header_template": (
            "Authorization: Bearer $plugin_supabase_access_token"
        ),
    }


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


def test_default_override_migrates_only_exact_obsolete_mcp_launchers(
    monkeypatch,
    tmp_path,
) -> None:
    clear_cache()
    override = tmp_path / "plugin_catalog.json"
    seed = json.loads(catalog_data._PACKAGE_SEED_PATH.read_text(encoding="utf-8"))
    github = next(item for item in seed["plugins"] if item["id"] == "github")
    supabase = next(item for item in seed["plugins"] if item["id"] == "supabase")
    github["mcp_server"] = {
        "transport": "stdio",
        "install": [
            "docker",
            "run",
            "-i",
            "--rm",
            "-e",
            "GITHUB_PERSONAL_ACCESS_TOKEN",
            "ghcr.io/github/github-mcp-server",
        ],
        "env_template": {
            "GITHUB_PERSONAL_ACCESS_TOKEN": "$plugin_github_access_token"
        },
    }
    supabase["mcp_server"] = {
        "transport": "stdio",
        "install": [
            "npx",
            "-y",
            "@supabase/mcp-server-supabase@latest",
            "--read-only",
            "--access-token",
            "$plugin_supabase_access_token",
        ],
        "env_template": {},
    }
    override.write_text(json.dumps(seed), encoding="utf-8")
    monkeypatch.setattr(catalog_data, "_DEFAULT_CATALOG_PATH", override)

    catalog = load_catalog()

    assert catalog.by_id("github").mcp_server["transport"] == "http"
    assert catalog.by_id("supabase").mcp_server["transport"] == "http"
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


def test_discord_is_bot_pat_channel_no_mcp() -> None:
    # AD-3 (2026-06-09): connecting Discord enables the in-repo bidirectional
    # channel (like Telegram), not a competing mcp-discord server that would
    # open a second Discord gateway over the same bot token.
    spec = _seed().by_id("discord")
    assert spec is not None
    assert spec.display_name == "Discord"
    assert spec.auth.mode == "pat_paste"
    assert spec.auth.auth_scheme == "bot"
    assert spec.mcp_server is None


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


def test_google_drive_uses_full_drive_scope_via_native_tool() -> None:
    # 2026-07-23: Drive moved off Google's hosted Drive MCP (a Workspace
    # Developer-Preview endpoint that 403s consumer @gmail.com accounts on every
    # data-plane call) onto a native REST tool. The catalog therefore carries a
    # native_tool + NO mcp_server, and the full 'drive' scope (all files, per the
    # "voller Zugriff" mandate) rather than the app-scoped drive.file.
    spec = _seed().by_id("google_drive")
    assert spec is not None
    assert spec.display_name == "Google Drive"
    assert spec.auth.mode == "oauth_pkce_loopback"
    assert spec.auth.scopes == ["https://www.googleapis.com/auth/drive"]
    assert spec.native_tool == "google_drive"
    assert spec.mcp_server is None
    assert spec.auth.scope_separator == "space"
    assert spec.auth.callback_path == ""
    assert spec.auth.offline_access is True


def test_gmail_pkce_loopback_full_mail_scope() -> None:
    # 2026-07-23: Gmail widened to the full mail.google.com scope (read + send +
    # organize + delete) so the native tool's modify/trash/delete actions have
    # the grant they need ("voller Zugriff" mandate).
    spec = _seed().by_id("gmail")
    assert spec is not None
    assert spec.display_name == "Gmail"
    assert spec.auth.mode == "oauth_pkce_loopback"
    assert "https://mail.google.com/" in spec.auth.scopes
    assert spec.auth.scope_separator == "space"
    assert spec.auth.callback_path == ""
    assert spec.auth.offline_access is True
    assert spec.native_tool == "gmail"
