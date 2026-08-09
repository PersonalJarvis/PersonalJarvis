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


def test_every_seed_category_is_declared_in_the_display_order() -> None:
    """`category` is a free string so a taxonomy change cannot break a user's
    local catalog on upgrade. This is what still catches a typo in the seed."""
    from jarvis.marketplace.catalog import CATEGORY_ORDER

    used = {p.category for p in _seed().plugins}
    assert used <= set(CATEGORY_ORDER), (
        f"seed categories missing from CATEGORY_ORDER: {sorted(used - set(CATEGORY_ORDER))}"
    )


def test_every_seed_plugin_states_its_longevity_note_when_limited() -> None:
    """A `provider_limited` card must say HOW often the user has to come back —
    the badge alone would be a warning without an answer."""
    limited = [p for p in _seed().plugins if p.longevity == "provider_limited"]
    assert limited, "the Google family is provider-limited; this test would be vacuous"
    for plugin in limited:
        assert plugin.longevity_note, f"{plugin.id} is provider_limited without a note"


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


def test_override_keeps_connection_details_but_not_presentation(
    monkeypatch, tmp_path
) -> None:
    """The user's own OAuth client survives; the product's own copy does not.

    A local catalog may legitimately carry a user's own OAuth client id. It
    must NOT freeze the card's category, wording or logo, or a taxonomy change
    would land on fresh installs only.
    """
    clear_cache()
    seed = json.loads(catalog_data._PACKAGE_SEED_PATH.read_text(encoding="utf-8"))
    seed_slack = next(item for item in seed["plugins"] if item["id"] == "slack")
    local_slack = json.loads(json.dumps(seed_slack))
    local_slack["description"] = "stale local wording"
    local_slack["category"] = "Retired Category"
    local_slack["auth"]["client_id"] = "1234.my-own-slack-client"
    override = tmp_path / "plugin_catalog.json"
    override.write_text(
        json.dumps({"version": 9, "schema_version": "ovr", "plugins": [local_slack]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(catalog_data, "_DEFAULT_CATALOG_PATH", override)

    slack = load_catalog().by_id("slack")

    assert slack.auth.client_id == "1234.my-own-slack-client"
    assert slack.description == seed_slack["description"]
    assert slack.category == seed_slack["category"]
    clear_cache()


def test_stale_override_still_receives_plugins_added_to_the_seed(
    monkeypatch, tmp_path
) -> None:
    """A new seed connector must reach installs that already have a data/ override.

    Before the merge the override replaced the seed wholesale, so a plugin
    added to the shipped catalog appeared only on FRESH installs while every
    existing machine — including the one it was developed on — kept serving its
    frozen list. The tests stayed green because they read the seed directly.
    """
    clear_cache()
    seed = json.loads(catalog_data._PACKAGE_SEED_PATH.read_text(encoding="utf-8"))
    stale = [item for item in seed["plugins"] if item["id"] == "github"]
    override = tmp_path / "plugin_catalog.json"
    override.write_text(
        json.dumps({"version": 1, "schema_version": "old", "plugins": stale}),
        encoding="utf-8",
    )
    monkeypatch.setattr(catalog_data, "_DEFAULT_CATALOG_PATH", override)

    ids = {p.id for p in load_catalog().plugins}

    assert "github" in ids, "the override's own entry must survive"
    assert {p["id"] for p in seed["plugins"]} <= ids, (
        "every seed plugin must surface through a stale override"
    )
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


def test_default_override_migrates_only_exact_obsolete_oauth_discovery(
    monkeypatch, tmp_path
) -> None:
    clear_cache()
    seed = json.loads(catalog_data._PACKAGE_SEED_PATH.read_text(encoding="utf-8"))
    clickup = next(item for item in seed["plugins"] if item["id"] == "clickup")
    canva = next(item for item in seed["plugins"] if item["id"] == "canva")
    clickup["auth"]["discovery_url"] = (
        "https://mcp.clickup.com/.well-known/oauth-authorization-server"
    )
    custom = "https://oauth.example.test/custom-protected-resource"
    canva["auth"]["discovery_url"] = custom
    override = tmp_path / "plugin_catalog.json"
    override.write_text(
        json.dumps(
            {"version": 1, "schema_version": "old", "plugins": [clickup, canva]}
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(catalog_data, "_DEFAULT_CATALOG_PATH", override)

    catalog = load_catalog()

    assert catalog.by_id("clickup").auth.discovery_url == (
        "https://mcp.clickup.com/.well-known/oauth-protected-resource"
    )
    assert catalog.by_id("canva").auth.discovery_url == custom
    clear_cache()


def test_seed_dcr_connectors_begin_at_protected_resource_metadata() -> None:
    catalog = _seed()
    expected = {
        "clickup": "https://mcp.clickup.com/.well-known/oauth-protected-resource",
        "canva": "https://mcp.canva.com/.well-known/oauth-protected-resource",
        "airtable": "https://mcp.airtable.com/.well-known/oauth-protected-resource",
        "cal_com": "https://mcp.cal.com/.well-known/oauth-protected-resource",
    }
    assert {
        plugin_id: catalog.by_id(plugin_id).auth.discovery_url
        for plugin_id in expected
    } == expected


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
