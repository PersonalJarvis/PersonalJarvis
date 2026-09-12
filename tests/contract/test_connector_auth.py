"""Authentication, registry and upload contracts for the service expansion."""

from __future__ import annotations

import base64
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import BackgroundTasks, HTTPException

from jarvis.marketplace.auth.oauth_pkce_loopback import (
    PkceLoopbackConfig,
    PkceLoopbackHandler,
    _PendingPkceFlow,
)
from jarvis.marketplace.catalog import PluginCatalog
from jarvis.marketplace.catalog_data import _PACKAGE_SEED_PATH, load_catalog
from jarvis.marketplace.plugin_registry import PluginToolRegistry
from jarvis.marketplace.token_store import InMemoryBackend, Tokens, TokenStore
from jarvis.plugins.tool.connected_server import ConnectedRestClient


@pytest.mark.asyncio
async def test_zoom_basic_auth_is_used_for_exchange_and_bound_client_refresh(monkeypatch):
    seen = []

    async def post(self, url, **kwargs):
        seen.append(kwargs)
        return httpx.Response(200, json={"access_token": "access", "refresh_token": "refresh"})

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    config = PkceLoopbackConfig(
        plugin_id="zoom",
        authorization_url="https://zoom.us/oauth/authorize",
        token_url="https://zoom.us/oauth/token",  # noqa: S106 - public endpoint
        client_id="client",
        callback_port=0,
        scopes=["meeting:write:meeting"],
        client_secret="fixture",  # noqa: S106 - synthetic credential
        client_auth_method="client_secret_basic",
    )
    handler = PkceLoopbackHandler(config)
    pending = _PendingPkceFlow(config, None, "verifier", "http://127.0.0.1:43891/oauth/callback")
    tokens = await handler._exchange(pending, code="code")
    await handler.refresh(tokens)
    for request in seen:
        assert (
            request["headers"]["Authorization"]
            == "Basic " + base64.b64encode(b"client:fixture").decode()
        )
        assert "client_secret" not in request["data"]
    assert seen[0]["data"]["code_verifier"] == "verifier"
    assert seen[1]["data"]["refresh_token"] == "refresh"  # noqa: S105 - synthetic token


@pytest.mark.asyncio
async def test_local_auth_persists_only_after_successful_capability_probe(monkeypatch):
    from jarvis.marketplace import amd_mcp
    from jarvis.ui.web import marketplace_routes as routes

    store = TokenStore(InMemoryBackend())
    monkeypatch.setattr(routes, "TokenStore", lambda: store)
    monkeypatch.setattr(routes, "load_catalog", lambda: load_catalog(_PACKAGE_SEED_PATH))
    monkeypatch.setattr(routes, "_refresh_plugin_in_live_registry", lambda _: None)

    def unavailable():
        raise RuntimeError("AMD SMI unavailable")

    monkeypatch.setattr(amd_mcp, "read_amd_status", unavailable)
    with pytest.raises(HTTPException) as error:
        await routes.connect_start("amd_gpu", BackgroundTasks())
    assert error.value.status_code == 409
    assert store.load("amd_gpu") is None
    monkeypatch.setattr(amd_mcp, "read_amd_status", lambda: {"metric": {"gpu": 0}})
    result = await routes.connect_start("amd_gpu", BackgroundTasks())
    assert result["kind"] == "local"
    assert store.load("amd_gpu").access == "local-enabled"


@pytest.mark.asyncio
async def test_connected_rest_tools_reach_live_registry_and_require_approval():
    plugin = load_catalog(_PACKAGE_SEED_PATH).by_id("outlook")
    store = TokenStore(InMemoryBackend())
    store.save("outlook", Tokens(access="fixture"))
    registry = PluginToolRegistry(
        catalog=PluginCatalog(version=1, schema_version="test", plugins=[plugin]), token_store=store
    )
    try:
        await registry.bootstrap()
        tools = {tool.name: tool for tool in registry.active_tools()}
        assert "outlook/send_mail" in tools
        assert tools["outlook/send_mail"].risk_tier == "ask"
        store.delete("outlook")
        result = await tools["outlook/send_mail"].execute({"body": {}}, SimpleNamespace())
        assert result.success is False
        await registry.refresh_plugin("outlook")
        assert registry.active_tools() == []
    finally:
        await registry.stop()


@pytest.mark.asyncio
async def test_youtube_resumable_upload_streams_bytes_and_defaults_private(tmp_path):
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"video-bytes")
    calls = []

    def respond(request):
        calls.append(request)
        if request.method == "POST":
            assert json.loads(request.content)["status"]["privacyStatus"] == "private"
            return httpx.Response(
                200,
                headers={
                    "Location": "https://www.googleapis.com/upload/youtube/v3/videos?upload_id=fixture"
                },
            )
        assert request.method == "PUT"
        assert request.content == b"video-bytes"
        return httpx.Response(201, json={"id": "uploaded-video"})

    client = ConnectedRestClient(
        "youtube_studio", "fixture", transport=httpx.MockTransport(respond)
    )
    try:
        result = await client.call(
            "upload_video", {"body": {"file_path": str(path.resolve()), "title": "Test"}}
        )
        assert result["id"] == "uploaded-video"
        assert len(calls) == 2
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_meta_resolves_page_token_without_exposing_it():
    seen = []

    def respond(request):
        seen.append(request.headers["Authorization"])
        if request.method == "GET":
            return httpx.Response(200, json={"access_token": "page-secret"})
        return httpx.Response(200, json={"id": "post", "access_token": "page-secret"})

    client = ConnectedRestClient("meta", "user-token", transport=httpx.MockTransport(respond))
    try:
        assert await client.call(
            "create_post", {"page_id": "page", "body": {"message": "Hello"}}
        ) == {"id": "post"}
        assert seen == ["Bearer user-token", "Bearer page-secret"]
    finally:
        await client.close()
