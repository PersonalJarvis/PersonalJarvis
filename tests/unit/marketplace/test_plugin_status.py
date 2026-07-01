"""`_plugin_status` maps stored Tokens to the four wire statuses the frontend
renders: connected / needs_reauth / not_connected / error."""

import pytest

from jarvis.marketplace.token_store import InMemoryBackend, Tokens, TokenStore
from jarvis.ui.web.marketplace_routes import _plugin_status


def test_status_connected_for_healthy_token():
    store = TokenStore(InMemoryBackend())
    store.save("p", Tokens(access="a"))
    assert _plugin_status("p", store) == "connected"


def test_status_needs_reauth_when_flagged():
    store = TokenStore(InMemoryBackend())
    store.save("p", Tokens(access="dead", needs_reauth=True))
    assert _plugin_status("p", store) == "needs_reauth"


def test_status_not_connected_when_absent():
    store = TokenStore(InMemoryBackend())
    assert _plugin_status("p", store) == "not_connected"


@pytest.mark.asyncio
async def test_list_plugins_sends_no_store_cache_header():
    """Embedded WebView2 must never serve a stale cached plugin list — the
    list endpoint must declare Cache-Control: no-store (regression guard for
    the 'plugins disappear in the desktop window' bug)."""
    from fastapi import Response

    from jarvis.ui.web.marketplace_routes import list_plugins

    resp = Response()
    await list_plugins(resp)
    assert resp.headers.get("cache-control") == "no-store"


@pytest.mark.asyncio
async def test_list_plugins_marks_native_tool_as_live_callable(monkeypatch):
    """Native Marketplace tools such as Gmail are live even without mcp_server."""
    from fastapi import Response

    from jarvis.marketplace.catalog import PluginCatalog, PluginSpec
    from jarvis.marketplace.token_store import TokenStore
    from jarvis.ui.web import marketplace_routes

    catalog = PluginCatalog(
        version=1,
        schema_version="test",
        plugins=[
            PluginSpec(
                id="gmail",
                display_name="Gmail",
                description="Mail",
                category="Communication",
                logo_slug="gmail",
                native_tool="gmail",
                auth={
                    "mode": "oauth_pkce_loopback",
                    "authorization_url": "https://accounts.example/auth",
                    "token_url": "https://accounts.example/token",
                    "client_id": "cid",
                    "scopes": ["scope"],
                },
            )
        ],
    )
    store = TokenStore(InMemoryBackend())
    store.save("gmail", Tokens(access="tok"))

    monkeypatch.setattr(marketplace_routes, "load_catalog", lambda: catalog)
    monkeypatch.setattr(marketplace_routes, "TokenStore", lambda: store)

    payload = await marketplace_routes.list_plugins(Response())

    assert payload["plugins"][0]["status"] == "connected"
    assert payload["plugins"][0]["live_callable"] is True


def _gmail_catalog():
    from jarvis.marketplace.catalog import PluginCatalog, PluginSpec

    return PluginCatalog(
        version=1,
        schema_version="test",
        plugins=[
            PluginSpec(
                id="gmail",
                display_name="Gmail",
                description="Mail",
                category="Communication",
                logo_slug="gmail",
                native_tool="gmail",
                auth={
                    "mode": "oauth_pkce_loopback",
                    "authorization_url": "https://accounts.example/auth",
                    "token_url": "https://accounts.example/token",
                    "client_id": "cid",
                    "scopes": ["scope"],
                },
            )
        ],
    )


@pytest.mark.asyncio
async def test_list_plugins_exposes_token_expiry_meta(monkeypatch):
    """The payload surfaces expires_at + last_refreshed so the UI can show an
    honest 'auto-refreshing / expiring soon' hint without re-deriving it."""
    from datetime import datetime, timezone

    from fastapi import Response

    from jarvis.ui.web import marketplace_routes

    exp = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    store = TokenStore(InMemoryBackend())
    store.save(
        "gmail",
        Tokens(
            access="tok",
            expires_at=exp,
            extra={"last_refreshed": "2026-06-30T10:00:00+00:00"},
        ),
    )
    monkeypatch.setattr(marketplace_routes, "load_catalog", _gmail_catalog)
    monkeypatch.setattr(marketplace_routes, "TokenStore", lambda: store)

    item = (await marketplace_routes.list_plugins(Response()))["plugins"][0]
    assert item["expires_at"] == exp.isoformat()
    assert item["last_refreshed"] == "2026-06-30T10:00:00+00:00"


@pytest.mark.asyncio
async def test_list_plugins_omits_expiry_meta_when_absent(monkeypatch):
    """A token with no expiry / never-refreshed (e.g. a PAT) carries null meta,
    never a fabricated timestamp."""
    from fastapi import Response

    from jarvis.ui.web import marketplace_routes

    store = TokenStore(InMemoryBackend())
    store.save("gmail", Tokens(access="tok"))
    monkeypatch.setattr(marketplace_routes, "load_catalog", _gmail_catalog)
    monkeypatch.setattr(marketplace_routes, "TokenStore", lambda: store)

    item = (await marketplace_routes.list_plugins(Response()))["plugins"][0]
    assert item["expires_at"] is None
    assert item["last_refreshed"] is None
