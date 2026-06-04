"""Catalog-backed connect helpers (Wave 2, #3 wiring).

`connected_plugin_ids` lists plugins with stored tokens; `build_handler_from_catalog`
maps a plugin id to its AuthHandler so the refresh scheduler can refresh it
without importing the FastAPI route module.
"""
from __future__ import annotations

import pytest

from jarvis.marketplace.auth import HostedMcpDcrHandler
from jarvis.marketplace.catalog import HostedMcpOAuthDcrAuth
from jarvis.marketplace.connect_helpers import (
    build_handler_from_catalog,
    connected_plugin_ids,
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
