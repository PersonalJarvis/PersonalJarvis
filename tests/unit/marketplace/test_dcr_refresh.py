"""Regression guard for the DCR token-refresh client_id binding.

Root cause of the "browser-OAuth plugins disconnect after a PC restart" bug:
``HostedMcpDcrHandler.refresh`` used to register a *fresh* client_id (DCR)
and present it when redeeming the stored ``refresh_token``. But a refresh
token is bound to the client_id that originally obtained it (OAuth 2.0 §6),
so the auth server replies ``400 invalid_grant`` -> the handler raised
``RuntimeError("revoked")`` -> the refresh scheduler *deleted* the keyring
entry -> the plugin showed "not_connected" after the next restart.

PAT plugins (GitHub) survived because they have no refresh token and a static
access token that is never touched. DCR plugins (Linear, Notion) died on the
first near-expiry refresh.

The fix: persist the original client_id (+ token_endpoint) alongside the
tokens and reuse it on refresh — never re-register. These tests pin that.
"""
from __future__ import annotations

import httpx
import pytest

from jarvis.marketplace.auth.oauth_dcr import (
    DcrConfig,
    HostedMcpDcrHandler,
    _PendingFlow,
)
from jarvis.marketplace.token_store import Tokens

TOKEN_ENDPOINT = "https://auth.example/token"  # noqa: S105 (a URL, not a secret)


def _handler() -> HostedMcpDcrHandler:
    return HostedMcpDcrHandler(
        DcrConfig(
            plugin_id="linear",
            discovery_url="https://mcp.linear.app/.well-known/oauth-protected-resource",
        )
    )


def _patch_post(monkeypatch: pytest.MonkeyPatch, captured: dict) -> None:
    """Capture the token request body/url and return a canned 200 response."""

    async def _fake_post(self, url, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        captured["url"] = url
        captured["data"] = kwargs.get("data")
        return httpx.Response(
            200,
            json={
                "access_token": "new-access",
                "refresh_token": "new-refresh",
                "expires_in": 3600,
            },
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)


@pytest.mark.asyncio
async def test_refresh_reuses_stored_client_id_and_never_reregisters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler = _handler()

    # The whole point: refresh must NOT mint a new client. Blow up if it tries.
    async def _boom_register(self, client, registration_endpoint, redirect_uri):  # noqa: ANN001
        raise AssertionError("refresh must not re-register a new client_id")

    monkeypatch.setattr(HostedMcpDcrHandler, "_register", _boom_register)

    captured: dict = {}
    _patch_post(monkeypatch, captured)

    current = Tokens(
        access="old-access",
        refresh="old-refresh",
        extra={"client_id": "client-A", "token_endpoint": TOKEN_ENDPOINT},
    )

    new = await handler.refresh(current)

    # Presented the ORIGINAL client_id against the stored token endpoint.
    assert captured["url"] == TOKEN_ENDPOINT
    assert captured["data"]["client_id"] == "client-A"
    assert captured["data"]["grant_type"] == "refresh_token"
    assert captured["data"]["refresh_token"] == "old-refresh"  # noqa: S105
    # New access token minted, and the client_id is preserved for next time.
    assert new.access == "new-access"
    assert new.extra["client_id"] == "client-A"
    assert new.extra["token_endpoint"] == TOKEN_ENDPOINT


@pytest.mark.asyncio
async def test_exchange_persists_client_id_and_token_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler = _handler()
    captured: dict = {}
    _patch_post(monkeypatch, captured)

    pending = _PendingFlow(
        config=handler._config,  # noqa: SLF001
        callback_server=None,  # _exchange does not touch the server
        code_verifier="verifier",
        state="state",
        redirect_uri="http://127.0.0.1:9999/callback",
        token_endpoint=TOKEN_ENDPOINT,
        client_id="client-A",
    )

    tokens = await handler._exchange(pending, code="auth-code")  # noqa: SLF001

    # Future refreshes need both of these to redeem the refresh token.
    assert tokens.extra["client_id"] == "client-A"
    assert tokens.extra["token_endpoint"] == TOKEN_ENDPOINT


@pytest.mark.asyncio
async def test_refresh_without_stored_client_id_is_soft_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A token minted before the fix has no client_id in extra. We cannot
    refresh it — but we must NOT report 'revoked', because the scheduler
    deletes 'revoked' entries and that would destroy a still-valid token.
    Raise a plain failure instead so the scheduler keeps the entry and the
    user can reconnect once to heal it."""
    handler = _handler()

    # Guard: must not reach the network at all for a legacy token.
    async def _boom_post(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        raise AssertionError("legacy refresh must not hit the network")

    monkeypatch.setattr(httpx.AsyncClient, "post", _boom_post)

    current = Tokens(access="old-access", refresh="old-refresh", extra={})

    with pytest.raises(RuntimeError) as excinfo:
        await handler.refresh(current)

    assert "revoked" not in str(excinfo.value)


@pytest.mark.asyncio
async def test_refresh_propagates_revocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A genuine ``invalid_grant`` (real revocation) must still surface as
    'revoked' so the scheduler drops it and the UI prompts a reconnect."""
    handler = _handler()

    async def _fake_post(self, url, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        return httpx.Response(400, text='{"error":"invalid_grant"}')

    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)

    current = Tokens(
        access="old-access",
        refresh="dead-refresh",
        extra={"client_id": "client-A", "token_endpoint": TOKEN_ENDPOINT},
    )

    with pytest.raises(RuntimeError, match="revoked"):
        await handler.refresh(current)
