"""Hosting-provider calls over a mock transport, and the REST surface."""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.computers import cloud
from jarvis.computers.service import ComputerService
from jarvis.ui.web import computers_routes


async def test_hostinger_attach_creates_the_key_once_and_attaches_it(secret_box) -> None:  # noqa: ANN001
    secret_box.set("hostinger_api_token", "tok-123456")
    calls: list[tuple[str, str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        calls.append((request.method, request.url.path, body))
        assert request.headers["Authorization"] == "Bearer tok-123456"
        if request.url.path == "/api/vps/v1/public-keys" and request.method == "GET":
            return httpx.Response(200, json={"data": [], "meta": {"per_page": 15, "total": 0}})
        if request.url.path == "/api/vps/v1/public-keys":
            return httpx.Response(200, json={"id": 325, "name": "Personal Jarvis", "key": "k"})
        return httpx.Response(200, json={"id": 1, "state": "success"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await cloud.attach_public_key("hostinger", "17923", "ssh-ed25519 AAAA me", client=client)

    assert calls[-1] == ("POST", "/api/vps/v1/public-keys/attach/17923", {"ids": [325]})


async def test_refused_token_becomes_a_sentence_without_the_body(secret_box) -> None:  # noqa: ANN001
    secret_box.set("hetzner_api_token", "bad-token")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "secret account detail"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(cloud.CloudError) as caught:
            await cloud.list_servers("hetzner", client=client)

    assert "refused the API token" in caught.value.message
    assert "secret account detail" not in caught.value.message


async def test_digitalocean_follows_pages(secret_box) -> None:  # noqa: ANN001
    secret_box.set("digitalocean_api_token", "do-token")

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        droplet = {"id": page, "name": f"d{page}", "status": "active", "networks": {"v4": []}}
        links = {"pages": {"next": "x"}} if page == 1 else {"pages": {}}
        return httpx.Response(200, json={"droplets": [droplet], "links": links})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        servers = await cloud.list_servers("digitalocean", client=client)

    assert [s.name for s in servers] == ["d1", "d2"]


@pytest.fixture
def client(computer_service: ComputerService, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(computers_routes, "get_service", lambda: computer_service)
    app = FastAPI()
    app.include_router(computers_routes.router)
    return TestClient(app)


def test_routes_list_identity_and_providers(client: TestClient) -> None:
    assert client.get("/api/computers").json() == {"computers": []}

    ident = client.get("/api/computers/identity").json()
    assert ident["public_key"].startswith("ssh-ed25519 ")
    assert ident["fingerprint"].startswith("SHA256:")
    # The identity is stable: the second read returns the same key.
    assert client.get("/api/computers/identity").json()["public_key"] == ident["public_key"]

    providers = {p["id"]: p for p in client.get("/api/computers/cloud").json()["providers"]}
    assert set(providers) == {"hostinger", "hetzner", "digitalocean", "vultr", "linode"}
    assert providers["hostinger"]["attaches_keys"] is True
    assert providers["hostinger"]["connected"] is False


def test_routes_validate_and_404(client: TestClient) -> None:
    bad = client.post("/api/computers", json={"name": "x", "host": "not a host!"})
    assert bad.status_code == 400
    assert "IP address" in bad.json()["detail"]["message"]

    assert client.get("/api/computers/c_missing").status_code == 404
    assert (
        client.post("/api/computers/cloud/aws/import", json={"server_id": "1"}).status_code == 404
    )
    assert client.post("/api/computers/local", json={"name": "Bad Name"}).status_code in (400, 409)


def test_rename_and_remove(client: TestClient, computer_service: ComputerService) -> None:
    created = client.post(
        "/api/computers", json={"name": "old", "host": "127.0.0.1", "port": 1}
    ).json()
    assert created["health"]["status"] == "offline"

    renamed = client.patch(f"/api/computers/{created['id']}", json={"name": "  New   name "})
    assert renamed.json()["name"] == "New name"

    assert client.delete(f"/api/computers/{created['id']}").json() == {"removed": True}
    assert computer_service.all() == []
