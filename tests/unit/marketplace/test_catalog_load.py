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
        assert "client_secret" not in wire["auth"], (
            f"{plugin.id}: frontend wire shape must not expose OAuth client_secret"
        )


def test_pkce_client_secret_is_server_only() -> None:
    from jarvis.marketplace.catalog import OAuthPkceLoopbackAuth

    test_client_secret = "unit-test-client-secret"  # noqa: S105
    auth = OAuthPkceLoopbackAuth(
        mode="oauth_pkce_loopback",
        authorization_url="https://accounts.example/auth",
        token_url="https://accounts.example/token",  # noqa: S106
        client_id="cid",
        client_secret=test_client_secret,
        scopes=["scope"],
    )

    assert auth.client_secret == test_client_secret
    assert "client_secret" not in auth.model_dump(mode="json")


def test_pat_auth_scheme_defaults_to_bearer() -> None:
    from jarvis.marketplace.catalog import PatPasteAuth

    a = PatPasteAuth(
        mode="pat_paste",
        token_creation_url="https://x",  # noqa: S106
        token_prefix="",
        validation_endpoint="https://x",
        instruction_md="md",
    )
    assert a.auth_scheme == "bearer"


def test_pat_auth_scheme_accepts_bot_and_telegram_path() -> None:
    from jarvis.marketplace.catalog import PatPasteAuth

    for scheme in ("bot", "telegram_path"):
        a = PatPasteAuth(
            mode="pat_paste",
            token_creation_url="https://x",  # noqa: S106
            token_prefix="",
            validation_endpoint="https://x",
            instruction_md="md",
            auth_scheme=scheme,
        )
        assert a.auth_scheme == scheme
