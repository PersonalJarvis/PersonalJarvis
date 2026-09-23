"""Broker regressions against a controlled transport, not live OAuth evidence."""

from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from jarvis.marketplace.auth.base import pkce_pair
from jarvis.marketplace.broker_service import BrokerProvider, create_broker_app

START_EXTRA = {"loopback_uri": "http://127.0.0.1:43891/oauth/broker", "client_state": "d" * 43}


@pytest.fixture
def broker(tmp_path):
    requests = []

    def upstream(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "access_token": "test-access",
                "refresh_token": "test-refresh",
                "expires_in": 1,
            },
        )

    config = dict(
        base_url="https://publisher.example/oauth",
        database=tmp_path / "broker.db",
        encryption_key=Fernet.generate_key(),
        providers={
            "figma": BrokerProvider(
                "https://www.figma.com/oauth",
                "https://api.figma.com/v1/oauth/token",
                "test-client",
                "figma_secret",
                ("current_user:read",),
                basic_auth=True,
            )
        },
        secret_reader=lambda key: "test-confidential",
        transport=httpx.MockTransport(upstream),
    )
    return config, requests


def flow(client):
    verifier, challenge = pkce_pair()
    start = client.post("/start", json={**START_EXTRA, "provider": "figma", "challenge": challenge})
    assert start.status_code == 200
    body = start.json()
    query = parse_qs(urlsplit(body["authorization_url"]).query)
    assert query["code_challenge_method"] == ["S256"]
    assert query["redirect_uri"] == ["https://publisher.example/oauth/callback"]
    assert "test-confidential" not in start.text
    return {"flow_id": body["flow_id"], "verifier": verifier}, query["state"][0]


def test_callback_pkce_one_time_refresh_restart_disconnect(broker):
    config, requests = broker
    with TestClient(create_broker_app(**config)) as client:
        proof, state = flow(client)
        assert client.post("/redeem", json=proof).json()["state"] == "pending"
        assert client.get("/callback", params={"state": "wrong", "code": "c"}).status_code == 400
        callback = client.get(
            "/callback", params={"state": state, "code": "test-code"}, follow_redirects=False
        )
        assert callback.status_code == 302
        location = callback.headers["location"]
        assert location.startswith(START_EXTRA["loopback_uri"])
        assert "test-access" not in location and "test-refresh" not in location
        # Even the original verifier cannot redeem without the browser code.
        assert client.post("/redeem", json=proof).status_code == 403
        handoff = parse_qs(urlsplit(location).query)
        assert handoff["state"] == [START_EXTRA["client_state"]]
        proof["handoff_code"] = handoff["code"][0]
        assert client.get("/callback", params={"state": state, "code": "c"}).status_code == 400
        wrong = {**proof, "verifier": "x" * 43}
        assert client.post("/redeem", json=wrong).status_code == 403
        wrong_handoff = {**proof, "handoff_code": "x" * 43}
        assert client.post("/redeem", json=wrong_handoff).status_code == 403
        result = client.post("/redeem", json=proof)
        assert result.headers["cache-control"] == "no-store"
        grant = result.json()
        assert grant["access_token"] == "test-access"  # noqa: S105 - fake provider fixture
        assert "test-refresh" not in result.text and "test-confidential" not in result.text
        assert client.post("/redeem", json=proof).status_code == 410
        handle = grant["refresh_handle"]
    assert b"test-refresh" not in config["database"].read_bytes()
    with TestClient(create_broker_app(**config)) as restarted:
        assert (
            restarted.post("/refresh", json={"provider": "other", "handle": handle}).status_code
            == 403
        )
        refreshed = restarted.post("/refresh", json={"provider": "figma", "handle": handle})
        assert refreshed.status_code == 200 and len(requests) == 2
        assert "Basic " in requests[0].headers["authorization"]
        assert b"code_verifier=" in requests[0].content
        assert (
            restarted.post("/disconnect", json={"provider": "figma", "handle": handle}).status_code
            == 200
        )
        assert (
            restarted.post("/refresh", json={"provider": "figma", "handle": handle}).status_code
            == 401
        )


@pytest.mark.parametrize("outcome", ["denied", "cancelled", "expired"])
def test_cancel_timeout_retry(broker, outcome):
    config, requests = broker
    with TestClient(create_broker_app(**config)) as client:
        proof, state = flow(client)
        if outcome == "denied":
            client.get(
                "/callback",
                params={"state": state, "error": "access_denied-test"},
                follow_redirects=False,
            )
            assert client.post("/redeem", json=proof).json() == {
                "state": "error",
                "error": "denied",
            }
        elif outcome == "cancelled":
            assert client.post("/cancel", json=proof).status_code == 200
            assert (
                client.get("/callback", params={"state": state, "code": "late"}).status_code == 400
            )
        else:
            import sqlite3

            with sqlite3.connect(config["database"]) as db:
                db.execute("UPDATE flows SET expires=0")
            assert client.post("/redeem", json=proof).status_code == 410
        flow(client)
    assert requests == []


def test_validation_never_echoes_input(broker):
    config, _ = broker
    with TestClient(create_broker_app(**config)) as client:
        response = client.post("/refresh", json={"handle": "test-sensitive", "provider": "figma"})
        assert response.status_code == 422
        assert "test-sensitive" not in response.text


@pytest.mark.parametrize(
    "callback",
    [
        "https://unrelated.example/oauth/broker",
        "http://localhost:8888/oauth/broker",
        "http://127.0.0.1:0/oauth/broker",
        "http://127.0.0.1:8888/other",
        "http://127.0.0.1:8888/oauth/broker?redirect=https://unrelated.example",
        "http://user@127.0.0.1:8888/oauth/broker",
    ],
)
def test_broker_refuses_remote_or_ambiguous_handoff_destinations(broker, callback):
    config, requests = broker
    _, challenge = pkce_pair()
    with TestClient(create_broker_app(**config)) as client:
        response = client.post(
            "/start",
            json={
                **START_EXTRA,
                "provider": "figma",
                "challenge": challenge,
                "loopback_uri": callback,
            },
        )
        assert response.status_code == 422
    assert requests == []


def test_rejects_insecure_deployment(broker):
    config, _ = broker
    with pytest.raises(ValueError, match="HTTPS"):
        create_broker_app(**{**config, "base_url": "http://publisher.example"})


@pytest.mark.asyncio
async def test_desktop_broker_protocol_persistence_and_client_binding(broker):
    from jarvis.marketplace.auth.oauth_broker import OAuthBrokerHandler
    from jarvis.marketplace.catalog_data import load_catalog
    from jarvis.marketplace.token_store import InMemoryBackend, TokenStore

    config, _ = broker
    # The service is mounted at the root for this in-process transport.
    config["base_url"] = "https://publisher.example"
    app = create_broker_app(**config)
    transport = httpx.ASGITransport(app=app)
    handler = OAuthBrokerHandler("figma", config["base_url"], transport=transport)
    session = await handler.start(load_catalog().by_id("figma"))
    state = parse_qs(urlsplit(session.open_url).query)["state"][0]
    async with httpx.AsyncClient(transport=transport, base_url=config["base_url"]) as client:
        response = await client.get("/callback", params={"code": "test-code", "state": state})
        assert response.status_code == 302
    async with httpx.AsyncClient(trust_env=False) as browser:
        completed = await browser.get(response.headers["location"])
        assert completed.status_code == 200
    result = await handler.await_completion(session)
    assert result.error is None
    assert result.tokens.refresh != "test-refresh"
    assert "client_secret" not in result.tokens.extra
    store = TokenStore(InMemoryBackend())
    store.save("figma", result.tokens)
    # Restart with no current broker config still uses the saved issuing URL.
    restarted = OAuthBrokerHandler("figma", "", transport=transport)
    refreshed = await restarted.refresh(store.load("figma"))
    assert refreshed.extra["broker_url"] == config["base_url"]
    assert refreshed.extra["client_id"] == "test-client"
    assert refreshed.refresh == result.tokens.refresh


@pytest.mark.asyncio
async def test_desktop_refuses_unexpected_authorization_origin():
    from jarvis.marketplace.auth.oauth_broker import OAuthBrokerHandler
    from jarvis.marketplace.catalog_data import load_catalog

    def response(request):
        return httpx.Response(
            200,
            json={"flow_id": "test-flow", "authorization_url": "https://unrelated.example/login"},
        )

    handler = OAuthBrokerHandler(
        "figma", "https://publisher.example", transport=httpx.MockTransport(response)
    )
    with pytest.raises(RuntimeError, match="unexpected authorization destination"):
        await handler.start(load_catalog().by_id("figma"))


@pytest.mark.asyncio
async def test_slow_refresh_does_not_block_other_flows_or_resurrect_disconnect(broker):
    import asyncio

    config, _ = broker
    entered, release = asyncio.Event(), asyncio.Event()

    async def upstream(request):
        if b"grant_type=refresh_token" in request.content:
            entered.set()
            await release.wait()
        return httpx.Response(
            200,
            json={
                "access_token": "test-access",
                "refresh_token": "test-refresh",
                "expires_in": 1,
            },
        )

    app = create_broker_app(**{**config, "transport": httpx.MockTransport(upstream)})
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="https://publisher.example"
        ) as client:
            verifier, challenge = pkce_pair()
            started = (
                await client.post(
                    "/start", json={**START_EXTRA, "provider": "figma", "challenge": challenge}
                )
            ).json()
            state = parse_qs(urlsplit(started["authorization_url"]).query)["state"][0]
            completed = await client.get("/callback", params={"code": "test", "state": state})
            handoff = parse_qs(urlsplit(completed.headers["location"]).query)["code"][0]
            grant = (
                await client.post(
                    "/redeem",
                    json={
                        "flow_id": started["flow_id"],
                        "verifier": verifier,
                        "handoff_code": handoff,
                    },
                )
            ).json()
            body = {"provider": "figma", "handle": grant["refresh_handle"]}
            refreshing = asyncio.create_task(client.post("/refresh", json=body))
            await entered.wait()
            try:
                another = await asyncio.wait_for(
                    client.post(
                        "/start", json={**START_EXTRA, "provider": "figma", "challenge": challenge}
                    ),
                    1,
                )
                assert another.status_code == 200
                removed = await asyncio.wait_for(client.post("/disconnect", json=body), 1)
                assert removed.status_code == 200
            finally:
                release.set()
            assert (await refreshing).status_code == 401
            assert (await client.post("/refresh", json=body)).status_code == 401


@pytest.mark.parametrize(
    "plugin",
    [
        "outlook",
        "onedrive",
        "teams",
        "sharepoint",
        "onenote",
        "microsoft_todo",
        "azure",
        "slack",
        "zoom",
        "gitlab",
        "spotify",
        "salesforce",
        "google_cloud",
    ],
)
def test_publisher_public_clients_never_resolve_confidential_secrets(monkeypatch, plugin):
    from jarvis.marketplace.publisher_clients import resolve_publisher_client

    reads = []

    def secret(key, *args):
        reads.append(key)
        if key.startswith("publisher_"):
            return "test-public-id" if key.endswith("_id") else "test-confidential"
        return None

    monkeypatch.setattr("jarvis.core.config.get_secret", secret)
    client, confidential, source = resolve_publisher_client(plugin, "REPLACE_WITH_CLIENT", None)
    assert client == "test-public-id" and confidential is None and source == "publisher"
    assert not any(key.startswith("publisher_") and key.endswith("_secret") for key in reads)


@pytest.mark.parametrize("plugin", ["hubspot", "asana", "figma", "linkedin", "discord"])
def test_confidential_family_uses_broker_and_preserves_legacy_refresh(monkeypatch, plugin):
    from jarvis.marketplace.auth.oauth_broker import OAuthBrokerHandler
    from jarvis.marketplace.connect_helpers import build_handler_from_catalog

    monkeypatch.setattr("jarvis.core.config.get_secret", lambda *args: None)
    handler = build_handler_from_catalog(plugin)
    assert isinstance(handler, OAuthBrokerHandler)
    assert handler.legacy_handler is not None


@pytest.mark.asyncio
async def test_public_loopback_host_stays_registered_and_bound_to_loopback():
    from jarvis.marketplace.oauth_callback_server import OAuthCallbackServer

    server = OAuthCallbackServer(
        "test-state", callback_path="/oauth/callback", redirect_host="localhost"
    )
    await server.start()
    try:
        assert server.redirect_uri == f"http://localhost:{server.port}/oauth/callback"
        async with httpx.AsyncClient(trust_env=False) as client:
            rejected = await client.get(
                server.redirect_uri, params={"code": "test", "state": "wrong"}
            )
            assert rejected.status_code == 400
            with pytest.raises(RuntimeError, match="state mismatch"):
                await server.await_callback()
            await server.stop()
            server = OAuthCallbackServer(
                "test-state", callback_path="/oauth/callback", redirect_host="localhost"
            )
            await server.start()
            accepted = await client.get(
                server.redirect_uri, params={"code": "test", "state": "test-state"}
            )
            assert accepted.status_code == 200
            replay = await client.get(
                server.redirect_uri, params={"code": "test", "state": "test-state"}
            )
            assert replay.status_code == 400
        assert (await server.await_callback()).code == "test"
    finally:
        await server.stop()
    with pytest.raises(ValueError, match="loopback"):
        OAuthCallbackServer("test-state", redirect_host="example.com")
