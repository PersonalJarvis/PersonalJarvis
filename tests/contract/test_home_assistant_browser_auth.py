"""Portable Home Assistant browser grant, refresh, verification and cancellation."""

from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from jarvis.marketplace.auth.home_assistant import HomeAssistantHandler
from jarvis.marketplace.catalog_data import load_catalog
from jarvis.marketplace.revoke import revoke_tokens
from jarvis.marketplace.token_store import InMemoryBackend, TokenStore


@pytest.mark.asyncio
async def test_browser_grant_refresh_revoke_and_secure_storage():
    requests = []

    def provider(request):
        requests.append(request)
        if request.url.path == "/api/":
            assert request.headers["authorization"] == "Bearer fake-access"
            return httpx.Response(200, json={"message": "API running."})
        if request.url.path == "/auth/revoke":
            assert parse_qs(request.content.decode())["token"] == ["fake-refresh"]
            return httpx.Response(200)
        body = parse_qs(request.content.decode())
        assert "code_verifier" not in body
        if body["grant_type"] == ["refresh_token"]:
            return httpx.Response(200, json={"access_token": "renewed", "expires_in": 1800})
        return httpx.Response(
            200,
            json={
                "access_token": "fake-access",
                "refresh_token": "fake-refresh",
                "expires_in": 1800,
            },
        )

    transport = httpx.MockTransport(provider)
    handler = HomeAssistantHandler("http://homeassistant.local:8123/overview", transport=transport)
    session = await handler.start(None)
    params = parse_qs(urlsplit(session.open_url).query)
    assert "code_challenge" not in params
    redirect = params["redirect_uri"][0]
    assert params["client_id"][0] == redirect.rsplit("/oauth/callback", 1)[0]
    try:
        async with httpx.AsyncClient() as client:
            await client.get(redirect, params={"state": params["state"][0], "code": "fake-code"})
        result = await handler.await_completion(session)
        assert result.error is None
        assert result.tokens.extra["instance_url"] == "http://homeassistant.local:8123"
        store = TokenStore(InMemoryBackend())
        store.save("home_assistant", result.tokens)
        saved = store.load("home_assistant")
        refreshed = await HomeAssistantHandler(transport=transport).refresh(saved)
        assert refreshed.access == "renewed"
        assert refreshed.refresh == "fake-refresh"
        assert refreshed.extra == saved.extra
        assert (
            await revoke_tokens(
                load_catalog().by_id("home_assistant"), refreshed, transport=transport
            )
            == "revoked"
        )
        assert len(requests) == 4
    finally:
        await handler.cancel(session)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,payload", [(401, {"secret": "never display"}), (200, {"unexpected": True})]
)
async def test_failed_api_probe_never_connects(status, payload):
    def provider(request):
        if request.url.path == "/api/":
            return httpx.Response(status, json=payload)
        return httpx.Response(
            200, json={"access_token": "a", "refresh_token": "r", "expires_in": 1800}
        )

    handler = HomeAssistantHandler(
        "http://homeassistant.local:8123", transport=httpx.MockTransport(provider)
    )
    session = await handler.start(None)
    try:
        with pytest.raises(RuntimeError, match="API verification failed") as error:
            await handler._exchange(handler._pending[session.flow_id], code="fake")
        assert "never display" not in str(error.value)
    finally:
        await handler.cancel(session)
    assert not handler._pending


def test_builtin_catalog_uses_browser_primary():
    spec = load_catalog().by_id("home_assistant")
    assert spec.auth.mode == "instance_browser"
    assert spec.fallback_auth.mode == "pat_paste"


def test_legacy_builtin_auth_migrates_but_custom_address_does_not():
    from jarvis.marketplace.catalog_data import _merge_with_seed

    spec = load_catalog().by_id("home_assistant")
    seed = {"plugins": [spec.model_dump()]}
    legacy = {"id": "home_assistant", "auth": spec.fallback_auth.model_dump(exclude_defaults=True)}
    # The exact shipped shape omitted optional default fields.
    legacy["auth"] = {
        key: value
        for key, value in spec.fallback_auth.model_dump().items()
        if key
        in (
            "mode",
            "token_creation_url",
            "token_prefix",
            "validation_endpoint",
            "instruction_md",
            "instance_url",
        )
    }
    assert (
        _merge_with_seed({"plugins": [legacy]}, seed)["plugins"][0]["auth"]["mode"]
        == "instance_browser"
    )
    legacy["auth"]["validation_endpoint"] = "https://custom.example/api/"
    assert (
        _merge_with_seed({"plugins": [legacy]}, seed)["plugins"][0]["auth"]["mode"] == "pat_paste"
    )
