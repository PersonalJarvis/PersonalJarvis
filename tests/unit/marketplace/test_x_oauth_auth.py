"""X OAuth wire contracts for existing catalogs and public/confidential apps."""

import base64

import httpx
import pytest

from jarvis.marketplace.auth.oauth_pkce_loopback import (
    PkceLoopbackConfig,
    PkceLoopbackHandler,
    _PendingPkceFlow,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "endpoint", ["https://api.x.com/2/oauth2/token", "https://api.twitter.com/2/oauth2/token"]
)
@pytest.mark.parametrize("secret", [None, "synthetic-secret"])
async def test_x_exchange_and_refresh_auth(monkeypatch, endpoint, secret):
    requests = []

    async def post(self, url, **kwargs):
        requests.append(kwargs)
        return httpx.Response(
            200, json={"access_token": "fixture", "refresh_token": "fixture-refresh"}
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    config = PkceLoopbackConfig(
        plugin_id="x",
        authorization_url="https://x.com/i/oauth2/authorize",
        token_url=endpoint,
        client_id="synthetic-client",
        client_secret=secret,
        callback_port=0,
        scopes=["tweet.read"],
    )
    handler = PkceLoopbackHandler(config)
    tokens = await handler._exchange(
        _PendingPkceFlow(config, None, "verifier", "http://127.0.0.1/callback"), code="fixture-code"
    )
    await handler.refresh(tokens)
    assert len(requests) == 2
    for request in requests:
        assert "client_secret" not in request["data"]
        if secret:
            expected = base64.b64encode(f"synthetic-client:{secret}".encode()).decode()
            assert request["headers"]["Authorization"] == f"Basic {expected}"
        else:
            assert "Authorization" not in request["headers"]
    assert requests[0]["data"]["code_verifier"] == "verifier"
    assert requests[1]["data"]["grant_type"] == "refresh_token"
