"""Confidential DCR clients retain secure authentication across their grant lifecycle."""

import base64
import json
from urllib.parse import parse_qs

import httpx
import pytest

from jarvis.marketplace.auth import oauth_dcr
from jarvis.marketplace.catalog import PluginSpec
from jarvis.marketplace.revoke import revoke_tokens
from jarvis.marketplace.token_store import Tokens


class Callback:
    _expected_state = "test-state"
    redirect_uri = "http://127.0.0.1:12345/callback"

    async def start(self):
        pass

    async def stop(self):
        pass


@pytest.mark.parametrize("method", ["none", "client_secret_basic", "client_secret_post"])
@pytest.mark.asyncio
async def test_discovery_exchange_refresh_and_revoke_use_registered_auth(monkeypatch, method):
    requests = []
    registration_count = 0

    def serve(request):
        nonlocal registration_count
        requests.append(request)
        path = request.url.path
        if path == "/resource":
            return httpx.Response(200, json={"authorization_servers": ["https://auth.test"]})
        if path == "/.well-known/oauth-authorization-server":
            return httpx.Response(
                200,
                json={
                    "authorization_endpoint": "https://auth.test/authorize",
                    "token_endpoint": "https://auth.test/token",
                    "registration_endpoint": "https://auth.test/register",
                    "revocation_endpoint": "https://auth.test/revoke",
                    "token_endpoint_auth_methods_supported": [method],
                },
            )
        if path == "/register":
            registration_count += 1
            assert json.loads(request.content)["token_endpoint_auth_method"] == method
            return httpx.Response(
                201,
                json={
                    "client_id": "client:id",
                    "client_secret": "secret/value" if method != "none" else None,
                    "token_endpoint_auth_method": method,
                },
            )
        assert path in {"/token", "/revoke"}
        form = parse_qs(request.content.decode())
        if method == "client_secret_basic":
            expected = base64.b64encode(b"client%3Aid:secret%2Fvalue").decode()
            assert request.headers["authorization"] == "Basic " + expected
            assert "client_id" not in form and "client_secret" not in form
        else:
            assert "authorization" not in request.headers
            assert form["client_id"] == ["client:id"]
            if method == "client_secret_post":
                assert form["client_secret"] == ["secret/value"]
            else:
                assert "client_secret" not in form
        return httpx.Response(200, json={"access_token": "access", "refresh_token": "refresh"})

    transport = httpx.MockTransport(serve)
    client_type = httpx.AsyncClient
    monkeypatch.setattr(
        oauth_dcr.httpx, "AsyncClient", lambda **kw: client_type(**{**kw, "transport": transport})
    )
    monkeypatch.setattr(oauth_dcr, "make_callback_server", lambda *a, **kw: Callback())
    handler = oauth_dcr.HostedMcpDcrHandler(
        oauth_dcr.DcrConfig("test", "https://auth.test/resource")
    )
    session = await handler.start(object())
    assert "secret" not in repr(session)
    pending = handler._pending[session.flow_id]
    assert "secret/value" not in repr(pending)
    tokens = await handler._exchange(pending, code="code")
    assert tokens.extra["token_endpoint_auth_method"] == method
    assert tokens.extra.get("client_secret") == ("secret/value" if method != "none" else None)
    # Reconstruct from the private token blob, just as on application restart.
    restored = Tokens.from_json(tokens.to_json())
    refreshed = await handler.refresh(restored)
    assert refreshed.extra == tokens.extra
    spec = PluginSpec.model_validate(
        {
            "id": "test",
            "display_name": "Test",
            "description": "Test",
            "category": "Developer",
            "logo_slug": "test",
            "auth": {
                "mode": "hosted_mcp_oauth_dcr",
                "discovery_url": "https://auth.test/resource",
                "mcp_url": "https://mcp.test/",
            },
        }
    )
    assert await revoke_tokens(spec, refreshed, transport=transport) == "revoked"
    assert registration_count == 1
    await handler.cancel(session)


@pytest.mark.asyncio
async def test_registration_rejects_missing_confidential_secret():
    handler = oauth_dcr.HostedMcpDcrHandler(
        oauth_dcr.DcrConfig("test", "https://auth.test/resource")
    )
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            201,
            json={
                "client_id": "client",
                "token_endpoint_auth_method": "client_secret_basic",
            },
        )
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(RuntimeError, match="secret is missing"):
            await handler._register(
                client, "https://auth.test/register", "http://localhost/cb", "client_secret_basic"
            )


@pytest.mark.asyncio
async def test_revocation_never_logs_provider_error_bodies(caplog):
    spec = PluginSpec.model_validate(
        {
            "id": "test",
            "display_name": "Test",
            "description": "Test",
            "category": "Developer",
            "logo_slug": "test",
            "auth": {
                "mode": "hosted_mcp_oauth_dcr",
                "discovery_url": "https://auth.test/resource",
                "mcp_url": "https://mcp.test/",
            },
        }
    )
    tokens = Tokens(
        access="private-access", extra={"revocation_endpoint": "https://auth.test/revoke"}
    )
    transport = httpx.MockTransport(lambda request: httpx.Response(400, text="private-access"))
    with caplog.at_level("INFO"):
        assert await revoke_tokens(spec, tokens, transport=transport) == "failed"
    assert "private-access" not in caplog.text
