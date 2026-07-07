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


class _StubPkceHandler:
    """Stands in for ``PkceLoopbackHandler`` past the guard: constructing the
    real handler and awaiting ``start()`` would bind a real fixed TCP port
    (e.g. Slack's 3118) and park the handler/session in the process-global
    FlowRegistry singleton — flaky and never cleaned up. The stub raises a
    sentinel from ``start()`` which the route turns into its 502 path, proving
    control passed the 409 guard without touching any socket or registry."""

    _SENTINEL = "stub-pkce-handler-start-called"

    def __init__(self, config):
        self.config = config

    async def start(self, plugin_spec):
        raise RuntimeError(self._SENTINEL)


async def test_connect_start_rejects_placeholder_client(monkeypatch):
    spec = _pkce_spec("REPLACE_WITH_JARVIS_GOOGLE_CLIENT_ID")
    monkeypatch.setattr(mr, "load_catalog", lambda: _Catalog([spec]))
    with pytest.raises(HTTPException) as exc_info:
        await mr.connect_start(spec.id, BackgroundTasks())
    assert exc_info.value.status_code == 409
    # Contract: the detail BEGINS with the phrase (frontend renders it verbatim).
    assert str(exc_info.value.detail).lower().startswith("oauth client not configured")


async def test_connect_start_secret_override_beats_placeholder(monkeypatch):
    """A downloader-supplied <family>_oauth_client_id must pass the guard."""
    spec = _pkce_spec("REPLACE_WITH_JARVIS_GOOGLE_CLIENT_ID")
    monkeypatch.setattr(mr, "load_catalog", lambda: _Catalog([spec]))
    monkeypatch.setattr(
        "jarvis.marketplace.connect_helpers.resolve_pkce_client",
        lambda pid, cid, csec: ("real-client-id.apps.example", None),
    )
    monkeypatch.setattr(mr, "PkceLoopbackHandler", _StubPkceHandler)
    # The flow proceeds past the guard into the stubbed handler, whose start()
    # sentinel surfaces as the route's 502 — anything but the 409 guard is fine.
    with pytest.raises(HTTPException) as exc_info:
        await mr.connect_start(spec.id, BackgroundTasks())
    assert exc_info.value.status_code != 409
    assert _StubPkceHandler._SENTINEL in str(exc_info.value.detail)
