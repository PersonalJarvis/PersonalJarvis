"""Custom connector routes: add by URL, connect, remove.

Runs the real router with every data/ path redirected into tmp, an in-memory
token store, and the probe replaced by a fake that answers the way a given
server would. The point is the wiring: the entry a connector writes must be
an ordinary plugin to every route that already exists.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from jarvis.marketplace import catalog_data, custom_connector
from jarvis.marketplace.custom_connector import ConnectorError, ConnectorProbe
from jarvis.marketplace.token_store import InMemoryBackend, TokenStore
from jarvis.marketplace.usage_cards import loader as cards_loader
from jarvis.ui.web import marketplace_routes as routes

URL = "https://mcp.acme.example/mcp"


class FakeServer:
    """Answers probes like one MCP server; records the headers it was sent."""

    def __init__(self, probe: ConnectorProbe | None = None, *, accepts: str | None = None):
        self.probe = probe or ConnectorProbe(auth="none", transport="http", status=200)
        self.accepts = accepts
        self.calls: list[tuple[str, dict]] = []
        self.down = False

    async def __call__(self, url: str, *, headers=None, transport=None) -> ConnectorProbe:
        self.calls.append((url, dict(headers or {})))
        if self.down:
            raise ConnectorError("Jarvis could not reach mcp.acme.example.")
        if headers and self.accepts is not None:
            ok = self.accepts in headers.values()
            return ConnectorProbe(
                auth="none" if ok else "token", transport="http", status=200 if ok else 401
            )
        return self.probe


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(catalog_data, "_DEFAULT_CATALOG_PATH", tmp_path / "plugin_catalog.json")
    monkeypatch.setattr(cards_loader, "_DATA_CARDS_DIR", tmp_path / "usage_cards")
    store = TokenStore(InMemoryBackend())
    monkeypatch.setattr(routes, "TokenStore", lambda: store)
    refreshed: list[str] = []
    monkeypatch.setattr(routes, "_refresh_plugin_in_live_registry", refreshed.append)
    server = FakeServer()
    monkeypatch.setattr(custom_connector, "probe_connector", server)
    catalog_data.clear_cache()
    cards_loader.load_usage_card.cache_clear()
    yield {"store": store, "server": server, "refreshed": refreshed}
    catalog_data.clear_cache()
    cards_loader.load_usage_card.cache_clear()


def _client() -> httpx.AsyncClient:
    app = FastAPI()
    app.include_router(routes.router)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _add(client: httpx.AsyncClient, **body) -> httpx.Response:
    return await client.post("/api/marketplace/connectors", json={"url": URL, **body})


@pytest.mark.asyncio
async def test_an_added_connector_is_an_ordinary_plugin(env) -> None:
    async with _client() as client:
        added = await _add(client, name="Acme Tools")
        listed = await client.get("/api/marketplace/plugins")

    assert added.status_code == 200
    item = added.json()["plugin"]
    assert item["id"] == "acme-tools"
    assert item["source"] == "local"
    assert item["status"] == "not_connected"
    assert added.json()["detected"] == {"auth": "none", "transport": "http", "server_name": None}
    entry = next(p for p in listed.json()["plugins"] if p["id"] == "acme-tools")
    assert entry["auth"]["mode"] == "hosted_mcp_open"
    assert entry["mcp_server"]["url"] == URL
    assert env["refreshed"] == ["acme-tools"]


@pytest.mark.asyncio
async def test_the_same_server_cannot_be_added_twice(env) -> None:
    async with _client() as client:
        await _add(client, name="Acme")
        again = await _add(client, name="Acme again", url="mcp.acme.example/mcp")

    assert again.status_code == 409
    assert "already added as Acme" in again.json()["detail"]


@pytest.mark.asyncio
async def test_a_name_that_matches_a_built_in_gets_its_own_id(env) -> None:
    async with _client() as client:
        added = await _add(client, name="Notion")

    assert added.status_code == 200
    assert added.json()["plugin"]["id"] == "notion-2"


@pytest.mark.asyncio
async def test_an_unreachable_server_writes_nothing(env) -> None:
    env["server"].down = True
    async with _client() as client:
        refused = await _add(client, name="Acme")
        listed = await client.get("/api/marketplace/plugins")

    assert refused.status_code == 422
    assert "could not reach" in refused.json()["detail"]
    assert all(p["source"] != "local" for p in listed.json()["plugins"])


@pytest.mark.asyncio
async def test_a_bad_address_is_a_400_before_anything_is_contacted(env) -> None:
    async with _client() as client:
        refused = await _add(client, url="http://mcp.acme.example/mcp")

    assert refused.status_code == 400
    assert env["server"].calls == []


@pytest.mark.asyncio
async def test_an_open_connector_connects_after_one_answered_handshake(env) -> None:
    async with _client() as client:
        await _add(client, name="Acme")
        started = await client.post("/api/marketplace/plugins/acme/connect/start")
        listed = await client.get("/api/marketplace/plugins")

    assert started.status_code == 200
    assert started.json()["kind"] == "local"
    assert env["store"].load("acme") is not None
    assert next(p for p in listed.json()["plugins"] if p["id"] == "acme")["status"] == "connected"


@pytest.mark.asyncio
async def test_an_open_connector_that_now_wants_sign_in_is_not_marked_connected(env) -> None:
    async with _client() as client:
        await _add(client, name="Acme")
        env["server"].probe = ConnectorProbe(auth="token", transport="http", status=401)
        started = await client.post("/api/marketplace/plugins/acme/connect/start")

    assert started.status_code == 409
    assert env["store"].load("acme") is None


@pytest.mark.asyncio
async def test_a_token_connector_checks_the_key_with_an_mcp_handshake(env) -> None:
    env["server"].probe = ConnectorProbe(auth="token", transport="http", status=401)
    env["server"].accepts = "Bearer good-key"
    async with _client() as client:
        added = await _add(client, name="Acme")
        assert added.json()["plugin"]["auth"]["mode"] == "pat_paste"
        refused = await client.post(
            "/api/marketplace/plugins/acme/connect/pat", json={"token": "bad-key"}
        )
        accepted = await client.post(
            "/api/marketplace/plugins/acme/connect/pat", json={"token": "good-key"}
        )

    assert refused.status_code == 401
    assert accepted.status_code == 200
    assert env["store"].load("acme").access == "good-key"
    assert env["server"].calls[-1] == (URL, {"Authorization": "Bearer good-key"})


@pytest.mark.asyncio
async def test_a_custom_header_name_carries_the_key(env) -> None:
    env["server"].probe = ConnectorProbe(auth="token", transport="http", status=401)
    env["server"].accepts = "k-1"
    async with _client() as client:
        await _add(client, name="Acme", auth="token", header_name="X-API-Key")
        accepted = await client.post(
            "/api/marketplace/plugins/acme/connect/pat", json={"token": "k-1"}
        )

    assert accepted.status_code == 200
    assert env["server"].calls[-1][1] == {"X-API-Key": "k-1"}


@pytest.mark.asyncio
async def test_oauth_without_self_registration_is_refused_with_the_way_out(env) -> None:
    env["server"].probe = ConnectorProbe(
        auth="oauth", transport="http", status=401,
        discovery_url="https://mcp.acme.example/.well-known/oauth-authorization-server",
        registration=False,
    )
    async with _client() as client:
        refused = await _add(client, name="Acme")

    assert refused.status_code == 422
    assert "Access token" in refused.json()["detail"]


@pytest.mark.asyncio
async def test_a_connector_the_owner_added_can_be_removed(env) -> None:
    async with _client() as client:
        await _add(client, name="Acme")
        await client.post("/api/marketplace/plugins/acme/connect/start")
        removed = await client.delete("/api/marketplace/community/plugins/acme")
        listed = await client.get("/api/marketplace/plugins")

    assert removed.status_code == 200
    assert removed.json()["removed"] is True
    assert env["store"].load("acme") is None
    assert all(p["id"] != "acme" for p in listed.json()["plugins"])


@pytest.mark.asyncio
async def test_a_built_in_plugin_still_cannot_be_removed(env) -> None:
    async with _client() as client:
        refused = await client.delete("/api/marketplace/community/plugins/notion")

    assert refused.status_code == 409
