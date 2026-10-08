"""Real two-sided pairing over ASGI HTTP, including the global auth boundary."""

from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

import httpx
import pytest
from fastapi import FastAPI

from jarvis.computers import pairing
from jarvis.core.http_pool import HttpClientPool
from jarvis.ui.web import server_pairing_routes
from jarvis.ui.web.missions_auth import reset_tokens
from jarvis.ui.web.surface_security import SurfaceSecurity


@pytest.fixture
async def paired_environment(tmp_path, monkeypatch, secret_box):
    for name in ("get_secret", "set_secret", "delete_secret"):
        monkeypatch.setattr(pairing, name, getattr(secret_box, name.split("_")[0]))
    remote = pairing.ServerPairing(tmp_path / "remote.json")
    monkeypatch.setattr(server_pairing_routes, "get_pairing", lambda: remote)
    app = FastAPI()
    app.include_router(server_pairing_routes.router)

    @app.get("/api/private-proof")
    def private_ui():
        return {"authenticated": True}

    secured = SurfaceSecurity(
        app,
        trusted_hosts=["remote.test"],
        control_key_validator=lambda value: value == "owner-only-test-credential",
    )
    pool = HttpClientPool(transport=httpx.ASGITransport(app=secured))
    local = pairing.ServerPairing(tmp_path / "local.json", pool)
    yield remote, local, pool.client(), secret_box
    await local.pool.aclose()
    reset_tokens()


async def issue(client):
    response = await client.post(
        "https://remote.test/api/computers/pairing/code",
        headers={"Authorization": "Bearer owner-only-test-credential"},
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    return response.json()["code"]


async def test_pair_save_reload_check_open_and_revoke(paired_environment, monkeypatch):
    remote, local, client, box = paired_environment
    code = await issue(client)
    server = await local.add("remote.test", code)
    credential = box.get(pairing._slot(server.id))
    assert server.url == "https://remote.test" and server.online
    assert credential and credential != code
    assert code not in local.path.read_text() and credential not in local.path.read_text()
    assert code not in remote.path.read_text() and credential not in remote.path.read_text()
    # Both durable sides survive a new service instance. Codes and tickets do not.
    restarted_remote = pairing.ServerPairing(remote.path)
    monkeypatch.setattr(server_pairing_routes, "get_pairing", lambda: restarted_remote)
    reloaded = pairing.ServerPairing(local.path, local.pool)
    assert (await reloaded.check(server.id)).online
    assert (
        await client.get(
            "https://remote.test/api/private-proof",
            headers={
                "Authorization": "Bearer " + credential,
            },
        )
    ).status_code == 401
    link = urlsplit(await reloaded.launch(server.id))
    assert link.netloc == "remote.test" and not link.query
    page = await client.get("https://remote.test" + link.path)
    assert (
        page.status_code == 200 and "script-src 'sha256-" in page.headers["content-security-policy"]
    )
    response = await client.post("https://remote.test" + link.path, json={"ticket": link.fragment})
    assert response.status_code == 200
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "secure" in cookie
    assert (await client.get("https://remote.test/api/private-proof")).status_code == 200
    assert (
        await client.post("https://remote.test" + link.path, json={"ticket": link.fragment})
    ).status_code == 401
    await reloaded.remove(server.id)
    assert reloaded.servers() == []
    assert box.get(pairing._slot(server.id)) is None
    assert restarted_remote.clients() == []
    assert (
        await client.get(
            "https://remote.test/api/computers/pairing/status",
            headers={
                "Authorization": "Bearer " + credential,
            },
        )
    ).status_code == 401
    await local.pool.aclose()


async def test_boundary_requires_owner_for_issuing_codes_and_tls_for_redemption(paired_environment):
    _, local, client, _ = paired_environment
    for path, method in [("code", "POST"), ("clients", "GET"), ("servers", "GET")]:
        assert (
            await client.request(method, "https://remote.test/api/computers/pairing/" + path)
        ).status_code == 401
    code = await issue(client)
    response = await client.post(
        "http://remote.test/api/computers/pairing/redeem", json={"code": code}
    )
    assert response.status_code == 403
    response = await client.post(
        "https://remote.test/api/computers/pairing/redeem",
        json={"code": code},
        headers={"Origin": "https://untrusted.test"},
    )
    assert response.status_code == 403
    assert (await local.add("https://remote.test", code)).online
    await local.pool.aclose()


def test_one_code_can_only_be_consumed_once_and_expires(tmp_path, monkeypatch):
    service = pairing.ServerPairing(tmp_path / "state.json")
    code = service.issue_code()["code"]

    def redeem():
        try:
            return service.redeem(code, "test").credential
        except pairing.PairingError:
            return None

    with ThreadPoolExecutor(max_workers=4) as executor:
        assert sum(value is not None for value in executor.map(lambda _: redeem(), range(4))) == 1
    invitation = service.issue_code()
    monkeypatch.setattr(pairing.time, "time", lambda: invitation["expires_at"] + 1)
    with pytest.raises(pairing.PairingError, match="expired"):
        service.redeem(invitation["code"], "test")


async def test_bad_code_redirect_and_protocol_do_not_save(paired_environment):
    _, local, client, box = paired_environment
    with pytest.raises(pairing.PairingError):
        await local.add("remote.test", "wrong-code")
    assert not local.servers() and not box.values
    await local.pool.aclose()
    requests = []

    def redirect(request):
        requests.append(request)
        return httpx.Response(307, headers={"Location": "https://other.test/collect"})

    local.pool = HttpClientPool(transport=httpx.MockTransport(redirect))
    with pytest.raises(pairing.PairingError):
        await local.add("https://remote.test", "private-example-code")
    assert len(requests) == 1 and requests[0].url.host == "remote.test"
    assert not local.servers()
    await local.pool.aclose()
    local.pool = HttpClientPool(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "name": "other app",
                    "platform": "Linux",
                    "credential": "a" * 43,
                },
            )
        )
    )
    with pytest.raises(pairing.PairingError, match="incompatible"):
        await local.add("remote.test", "another-code")
    assert not local.servers()
    await local.pool.aclose()


async def test_offline_check_and_failed_credential_save_are_honest(paired_environment, monkeypatch):
    _, local, client, box = paired_environment
    monkeypatch.setattr(pairing, "set_secret", lambda key, value: False)
    with pytest.raises(pairing.PairingError, match="could not be saved"):
        await local.add("remote.test", await issue(client))
    assert not local.servers()
    monkeypatch.setattr(pairing, "set_secret", box.set)
    server = await local.add("remote.test", await issue(client))
    await local.pool.aclose()
    local.pool = HttpClientPool(transport=httpx.MockTransport(lambda request: httpx.Response(503)))
    assert not (await local.check(server.id)).online
    with pytest.raises(pairing.PairingError):
        await local.remove(server.id)
    assert len(local.servers()) == 1 and box.get(pairing._slot(server.id))
    await local.pool.aclose()


def test_revoked_grant_invalidates_pending_tickets(tmp_path, monkeypatch):
    service = pairing.ServerPairing(tmp_path / "state.json")
    grant = service.redeem(service.issue_code()["code"], "test")
    ticket = service.issue_ticket(grant.credential)
    service.revoke(service.clients()[0]["id"])
    with pytest.raises(pairing.PairingError):
        service.consume_ticket(ticket)
    with pytest.raises(pairing.PairingError):
        service.issue_ticket(grant.credential)
    state = json.loads(service.path.read_text())
    assert state["grants"] == []


async def test_disconnect_finishes_before_repairing_the_same_origin(paired_environment):
    remote, local, client, box = paired_environment
    original = await local.add("remote.test", await issue(client))
    next_code = await issue(client)
    arrived, release = asyncio.Event(), asyncio.Event()
    original_transport = local.pool._transport

    class GatedTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            if request.url.path.endswith("/disconnect"):
                arrived.set()
                await release.wait()
            return await original_transport.handle_async_request(request)

    await local.pool.aclose()
    local.pool = HttpClientPool(transport=GatedTransport())
    removal = asyncio.create_task(local.remove(original.id))
    await asyncio.wait_for(arrived.wait(), timeout=2)
    addition = asyncio.create_task(local.add("remote.test", next_code))
    await asyncio.sleep(0)
    assert not addition.done()
    release.set()
    await removal
    replacement = await addition
    assert local.servers() == [replacement]
    assert box.get(pairing._slot(replacement.id))
    assert len(remote.clients()) == 1


@pytest.mark.parametrize(
    "value",
    [
        "http://remote.test",
        "https://a:b@remote.test",
        "https://remote.test/?key=private",
        "file:///tmp/test",
        "https://remote.test/path",
    ],
)
def test_rejects_unsafe_server_origins(value):
    with pytest.raises(pairing.PairingError):
        pairing.normalize_server(value)


def test_openapi_pairs_request_and_response_models():
    app = FastAPI()
    app.include_router(server_pairing_routes.router)
    schema = app.openapi()
    operations = [
        operation["operationId"] for path in schema["paths"].values() for operation in path.values()
    ]
    assert len(set(operations)) == len(operations)
    assert "computer_servers_add" in operations and "computer_pairing_redeem" in operations
