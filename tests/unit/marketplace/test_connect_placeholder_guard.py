"""Placeholder OAuth clients must be rejected at connect/start (fresh-machine honesty).

A fresh install ships REPLACE_WITH_* client ids; firing them at the provider
produces a browser error page and a 300 s pending spinner. connect_start must
instead fail fast with an actionable 409.
"""

from __future__ import annotations

import pytest
from fastapi import BackgroundTasks, HTTPException

from jarvis.ui.web import marketplace_routes as mr


class _Catalog:
    """Minimal stand-in for ``PluginCatalog`` carrying a single plugin spec.

    ``connect_start`` calls the module-level ``load_catalog()`` and then
    ``catalog.by_id(plugin_id)`` — no separate spec-lookup symbol exists to
    monkeypatch, so tests replace ``load_catalog`` itself with a one-plugin
    catalog instead.
    """

    def __init__(self, specs):
        self.plugins = specs

    def by_id(self, plugin_id: str):
        return next((s for s in self.plugins if s.id == plugin_id), None)


def _pkce_spec(client_id: str):
    for spec in mr.load_catalog().plugins:
        if spec.auth is not None and getattr(spec.auth, "mode", "") == "oauth_pkce_loopback":
            return spec.model_copy(
                update={"auth": spec.auth.model_copy(update={"client_id": client_id})}
            )
    pytest.skip("no oauth_pkce_loopback plugin in catalog")


async def test_connect_start_rejects_placeholder_client(monkeypatch):
    spec = _pkce_spec("REPLACE_WITH_JARVIS_GOOGLE_CLIENT_ID")
    monkeypatch.setattr(mr, "load_catalog", lambda: _Catalog([spec]))
    with pytest.raises(HTTPException) as exc_info:
        await mr.connect_start(spec.id, BackgroundTasks())
    assert exc_info.value.status_code == 409
    assert "oauth client not configured" in str(exc_info.value.detail).lower()


async def test_connect_start_secret_override_beats_placeholder(monkeypatch):
    """A downloader-supplied <family>_oauth_client_id must pass the guard."""
    spec = _pkce_spec("REPLACE_WITH_JARVIS_GOOGLE_CLIENT_ID")
    monkeypatch.setattr(mr, "load_catalog", lambda: _Catalog([spec]))
    monkeypatch.setattr(
        "jarvis.marketplace.connect_helpers.resolve_pkce_client",
        lambda pid, cid, csec: ("real-client-id.apps.example", None),
    )
    # The flow proceeds past the guard; it may fail LATER for unrelated
    # reasons (no browser/port in CI) — anything but the 409 guard is fine.
    try:
        await mr.connect_start(spec.id, BackgroundTasks())
    except HTTPException as exc:
        assert exc.status_code != 409
