"""Catalog-backed connect helpers (Wave 2, #3 wiring).

`connected_plugin_ids` lists plugins with stored tokens; `build_handler_from_catalog`
maps a plugin id to its AuthHandler so the refresh scheduler can refresh it
without importing the FastAPI route module.
"""
from __future__ import annotations

import pytest

from jarvis.marketplace.auth import HostedMcpDcrHandler, PkceLoopbackHandler
from jarvis.marketplace.catalog import HostedMcpOAuthDcrAuth, OAuthPkceLoopbackAuth
from jarvis.marketplace.connect_helpers import (
    build_handler_from_catalog,
    connected_plugin_ids,
    is_placeholder_client_id,
    resolve_pkce_client,
)
from jarvis.marketplace.token_store import InMemoryBackend, Tokens, TokenStore


class _Spec:
    def __init__(self, plugin_id: str, auth: object = None) -> None:
        self.id = plugin_id
        self.auth = auth


class _Catalog:
    def __init__(self, specs: list[_Spec]) -> None:
        self.plugins = specs

    def by_id(self, plugin_id: str) -> _Spec | None:
        return next((s for s in self.plugins if s.id == plugin_id), None)


def test_connected_plugin_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "jarvis.marketplace.catalog_data.load_catalog",
        lambda: _Catalog([_Spec("notion"), _Spec("linear"), _Spec("github")]),
    )
    store = TokenStore(InMemoryBackend())
    store.save("notion", Tokens(access="a"))
    store.save("github", Tokens(access="b"))
    assert set(connected_plugin_ids(store)) == {"notion", "github"}


def test_build_handler_unknown_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "jarvis.marketplace.catalog_data.load_catalog", lambda: _Catalog([])
    )
    assert build_handler_from_catalog("nope") is None


def test_build_handler_pat_paste_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    # A spec whose auth is not an OAuth handler (e.g. pat_paste) has no
    # refreshable handler.
    monkeypatch.setattr(
        "jarvis.marketplace.catalog_data.load_catalog",
        lambda: _Catalog([_Spec("vercel", auth=object())]),
    )
    assert build_handler_from_catalog("vercel") is None


def test_build_handler_dcr_returns_dcr_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth = HostedMcpOAuthDcrAuth(
        mode="hosted_mcp_oauth_dcr",
        discovery_url="https://notion.test/.well-known/oauth-protected-resource",
        mcp_url="https://mcp.notion.test",
    )
    monkeypatch.setattr(
        "jarvis.marketplace.catalog_data.load_catalog",
        lambda: _Catalog([_Spec("notion", auth=auth)]),
    )
    handler = build_handler_from_catalog("notion")
    assert isinstance(handler, HostedMcpDcrHandler)
    assert handler.plugin_id == "notion"


# ---------------------------------------------------------------------------
# Google OAuth client resolution (live 2026-06-07 Gmail bug)
# ---------------------------------------------------------------------------
#
# The shipped catalog cannot carry the maintainer's real Google OAuth client, so
# gmail/google_drive ship a `REPLACE_WITH_...` placeholder client_id. The real
# client must be supplied via a secret (survives catalog re-sync, never tracked).
# Without this, refresh sends the placeholder and Google answers
# `invalid_client: "The OAuth client was not found."` — the token then rots.

_PKCE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_PLACEHOLDER = "REPLACE_WITH_JARVIS_GOOGLE_CLIENT_ID"


def _gmail_auth(client_id: str = _PLACEHOLDER) -> OAuthPkceLoopbackAuth:
    return OAuthPkceLoopbackAuth(
        mode="oauth_pkce_loopback",
        authorization_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url=_PKCE_TOKEN_URL,
        client_id=client_id,
        scopes=["https://www.googleapis.com/auth/gmail.readonly"],
        offline_access=True,
    )


def test_is_placeholder_client_id() -> None:
    assert is_placeholder_client_id(_PLACEHOLDER) is True
    assert is_placeholder_client_id("") is True
    assert is_placeholder_client_id(None) is True
    assert is_placeholder_client_id("   ") is True
    assert is_placeholder_client_id("YOUR_CLIENT_ID_HERE") is True
    assert is_placeholder_client_id("123-abc.apps.googleusercontent.com") is False


def test_gmail_handler_uses_google_secret_over_catalog_placeholder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "jarvis.marketplace.catalog_data.load_catalog",
        lambda: _Catalog([_Spec("gmail", auth=_gmail_auth())]),
    )

    def fake_secret(key: str, env_fallback: str | None = None) -> str | None:
        return {
            "google_oauth_client_id": "real-123.apps.googleusercontent.com",
            "google_oauth_client_secret": "GOCSPX-realsecret",
        }.get(key)

    monkeypatch.setattr("jarvis.core.config.get_secret", fake_secret)

    handler = build_handler_from_catalog("gmail")
    assert isinstance(handler, PkceLoopbackHandler)
    assert handler._config.client_id == "real-123.apps.googleusercontent.com"
    assert handler._config.client_secret == "GOCSPX-realsecret"


def test_gmail_handler_falls_back_to_catalog_when_no_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No secret configured -> catalog value is used unchanged. A real client_id
    # in the catalog still works; the placeholder still builds a handler (it is
    # NOT dropped to None, so a refresh attempt happens and the scheduler can
    # flag needs_reauth — never a silent green "connected").
    real_in_catalog = "catalog-456.apps.googleusercontent.com"
    monkeypatch.setattr(
        "jarvis.marketplace.catalog_data.load_catalog",
        lambda: _Catalog([_Spec("gmail", auth=_gmail_auth(client_id=real_in_catalog))]),
    )
    monkeypatch.setattr(
        "jarvis.core.config.get_secret", lambda key, env_fallback=None: None
    )

    handler = build_handler_from_catalog("gmail")
    assert isinstance(handler, PkceLoopbackHandler)
    assert handler._config.client_id == real_in_catalog


def test_non_google_pkce_ignores_google_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A non-Google PKCE plugin (e.g. Slack) keeps its own real catalog client_id
    # and must NOT be hijacked by the shared google_oauth secret.
    slack_auth = OAuthPkceLoopbackAuth(
        mode="oauth_pkce_loopback",
        authorization_url="https://slack.com/oauth/v2/authorize",
        token_url="https://slack.com/api/oauth.v2.access",
        client_id="slack-real.id",
        scopes=["chat:write"],
    )
    monkeypatch.setattr(
        "jarvis.marketplace.catalog_data.load_catalog",
        lambda: _Catalog([_Spec("slack", auth=slack_auth)]),
    )
    monkeypatch.setattr(
        "jarvis.core.config.get_secret",
        lambda key, env_fallback=None: "google-leak.id"
        if "google" in key
        else None,
    )

    handler = build_handler_from_catalog("slack")
    assert isinstance(handler, PkceLoopbackHandler)
    assert handler._config.client_id == "slack-real.id"


def test_resolve_pkce_client_google_prefers_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # resolve_pkce_client is shared by BOTH the connect route and the refresh
    # scheduler, so the operator's real Google client is used at connect-time
    # AND refresh-time — a placeholder catalog client is overridden in both.
    monkeypatch.setattr(
        "jarvis.core.config.get_secret",
        lambda key, env_fallback=None: {
            "google_oauth_client_id": "rid.apps.googleusercontent.com",
            "google_oauth_client_secret": "GOCSPX-x",
        }.get(key),
    )
    cid, csec = resolve_pkce_client("gmail", _PLACEHOLDER, None)
    assert cid == "rid.apps.googleusercontent.com"
    assert csec == "GOCSPX-x"


def test_resolve_pkce_client_non_google_passthrough(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "jarvis.core.config.get_secret",
        lambda key, env_fallback=None: "should-not-be-used",
    )
    cid, csec = resolve_pkce_client("slack", "slack.id", "slack.secret")
    assert cid == "slack.id"
    assert csec == "slack.secret"
