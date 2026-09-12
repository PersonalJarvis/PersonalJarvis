"""The real catalog endpoint exposes optional local availability across OSes."""

import pytest
from fastapi import Response

from jarvis.marketplace.catalog import PluginCatalog
from jarvis.marketplace.catalog_data import load_catalog
from jarvis.marketplace.token_store import InMemoryBackend, TokenStore
from jarvis.ui.web import marketplace_routes as routes


@pytest.mark.parametrize("platform", ["win32", "darwin", "linux"])
async def test_catalog_marks_missing_amd_capability_before_connect(monkeypatch, platform):
    from jarvis.marketplace import amd_mcp

    spec = load_catalog().by_id("amd_gpu")
    catalog = PluginCatalog(version=1, schema_version="test", plugins=[spec])
    monkeypatch.setattr(routes, "load_catalog", lambda: catalog)
    monkeypatch.setattr(routes, "TokenStore", lambda: TokenStore(InMemoryBackend()))
    monkeypatch.setattr(routes, "_mcp_live", lambda *args, **kwargs: (False, None))
    monkeypatch.setattr(amd_mcp.sys, "platform", platform)
    monkeypatch.setattr(amd_mcp.shutil, "which", lambda name: None)
    payload = await routes.list_plugins(Response())
    assert payload["plugins"][0]["unavailable_reason"]
    assert payload["plugins"][0]["status"] == "not_connected"
