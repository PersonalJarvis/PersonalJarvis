"""Disconnect must also end the grant AT THE PROVIDER.

Removing a plugin used to delete only the local token, leaving the
authorization alive in the user's account — the provider kept listing the app
as connected forever. `revocation_url` sat unused in the catalog schema the
whole time.

The second half of the contract matters just as much: revocation is
best-effort. The user asked for the credential to be removed, so an unreachable
or non-compliant provider must never turn a disconnect into a failure.
"""

from __future__ import annotations

import httpx
import pytest

from jarvis.marketplace.catalog import PluginSpec
from jarvis.marketplace.revoke import revoke_tokens
from jarvis.marketplace.token_store import Tokens


def _pkce_spec(revocation_url: str | None) -> PluginSpec:
    return PluginSpec.model_validate(
        {
            "id": "demo",
            "display_name": "Demo",
            "description": "d",
            "category": "Developer",
            "logo_slug": "demo",
            "auth": {
                "mode": "oauth_pkce_loopback",
                "authorization_url": "https://example.test/authorize",
                "token_url": "https://example.test/token",
                "revocation_url": revocation_url,
                "client_id": "cid",
                "scopes": ["read"],
            },
        }
    )


def _transport(record: list[httpx.Request], status: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        record.append(request)
        return httpx.Response(status)

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_revokes_the_refresh_token_not_just_the_access_token() -> None:
    """RFC 7009 §2.1: revoking the refresh token kills the whole grant.
    Revoking only the access token would leave it renewable."""
    seen: list[httpx.Request] = []
    outcome = await revoke_tokens(
        _pkce_spec("https://example.test/revoke"),
        Tokens(access="at", refresh="rt", extra={"client_id": "cid"}),
        transport=_transport(seen),
    )

    assert outcome == "revoked"
    assert len(seen) == 1
    body = seen[0].content.decode()
    assert "token=rt" in body
    assert "token_type_hint=refresh_token" in body
    assert "client_id=cid" in body


@pytest.mark.asyncio
async def test_falls_back_to_the_access_token_when_there_is_no_refresh() -> None:
    seen: list[httpx.Request] = []
    outcome = await revoke_tokens(
        _pkce_spec("https://example.test/revoke"),
        Tokens(access="at"),
        transport=_transport(seen),
    )

    assert outcome == "revoked"
    assert "token=at" in seen[0].content.decode()


@pytest.mark.asyncio
async def test_unknown_token_still_counts_as_revoked() -> None:
    """RFC 7009 §2.2 returns 200 for a token the server does not recognize —
    an already-dead grant is exactly the state we wanted."""
    outcome = await revoke_tokens(
        _pkce_spec("https://example.test/revoke"),
        Tokens(access="at", refresh="rt"),
        transport=_transport([], status=200),
    )
    assert outcome == "revoked"


@pytest.mark.asyncio
async def test_a_provider_without_an_endpoint_is_reported_not_attempted() -> None:
    seen: list[httpx.Request] = []
    outcome = await revoke_tokens(
        _pkce_spec(None),
        Tokens(access="at", refresh="rt"),
        transport=_transport(seen),
    )

    assert outcome == "unsupported"
    assert seen == [], "nothing to call, so nothing may be called"


@pytest.mark.asyncio
async def test_a_dcr_plugin_uses_the_endpoint_discovered_at_connect_time() -> None:
    """A DCR client is ephemeral, so its revocation endpoint is only knowable
    from the discovery document — it is persisted with the tokens."""
    seen: list[httpx.Request] = []
    spec = PluginSpec.model_validate(
        {
            "id": "dcr",
            "display_name": "Dcr",
            "description": "d",
            "category": "Developer",
            "logo_slug": "dcr",
            "auth": {
                "mode": "hosted_mcp_oauth_dcr",
                "discovery_url": "https://mcp.example.test/mcp",
                "mcp_url": "https://mcp.example.test/mcp",
            },
        }
    )

    outcome = await revoke_tokens(
        spec,
        Tokens(
            access="at",
            refresh="rt",
            extra={"revocation_endpoint": "https://as.example.test/revoke"},
        ),
        transport=_transport(seen),
    )

    assert outcome == "revoked"
    assert str(seen[0].url) == "https://as.example.test/revoke"


@pytest.mark.asyncio
async def test_an_unreachable_provider_never_blocks_the_disconnect() -> None:
    def explode(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host", request=request)

    outcome = await revoke_tokens(
        _pkce_spec("https://example.test/revoke"),
        Tokens(access="at", refresh="rt"),
        transport=httpx.MockTransport(explode),
    )

    assert outcome == "failed", "reported honestly, and NOT raised"


@pytest.mark.asyncio
async def test_a_refusal_is_reported_rather_than_swallowed() -> None:
    outcome = await revoke_tokens(
        _pkce_spec("https://example.test/revoke"),
        Tokens(access="at", refresh="rt"),
        transport=_transport([], status=400),
    )
    assert outcome == "failed"
