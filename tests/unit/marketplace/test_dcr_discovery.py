"""Auth-server metadata discovery for issuers WITH a path component.

Stripe's protected-resource doc points at the authorization server
``https://access.stripe.com/mcp`` (a path issuer). RFC 8414 §3 inserts
``/.well-known/oauth-authorization-server`` BETWEEN the host and the path, so the
metadata lives at ``https://access.stripe.com/.well-known/oauth-authorization-server/mcp``.
The old naive append form (``.../mcp/.well-known/...``) returns 404 — which broke
the Stripe connect flow with a live "connect-start failed: 404" error. Also
covers Asana (``mcp.asana.com/v2``). Path-less issuers (Notion/Linear) are
unaffected.
"""

import httpx
import pytest

from jarvis.marketplace.auth.oauth_dcr import (
    DcrConfig,
    HostedMcpDcrHandler,
    _well_known_candidates,
)


def test_path_issuer_insert_form_is_first():
    c = _well_known_candidates("https://access.stripe.com/mcp")
    assert c[0] == (
        "https://access.stripe.com/.well-known/oauth-authorization-server/mcp"
    )


def test_path_issuer_keeps_append_as_fallback():
    c = _well_known_candidates("https://access.stripe.com/mcp")
    assert (
        "https://access.stripe.com/mcp/.well-known/oauth-authorization-server" in c
    )


def test_pathless_issuer_unchanged():
    c = _well_known_candidates("https://mcp.notion.com")
    assert c[0] == "https://mcp.notion.com/.well-known/oauth-authorization-server"


def test_trailing_slash_normalized():
    c = _well_known_candidates("https://access.stripe.com/mcp/")
    assert c[0] == (
        "https://access.stripe.com/.well-known/oauth-authorization-server/mcp"
    )


@pytest.mark.asyncio
async def test_discover_handles_stripe_style_path_issuer():
    """The exact Stripe failure reproduced: append form 404s, insert form 200s."""
    INSERT = "https://access.stripe.com/.well-known/oauth-authorization-server/mcp"
    APPEND = "https://access.stripe.com/mcp/.well-known/oauth-authorization-server"
    META = {
        "issuer": "https://access.stripe.com/mcp",
        "authorization_endpoint": "https://access.stripe.com/mcp/oauth2/authorize",
        "token_endpoint": "https://access.stripe.com/mcp/oauth2/token",
        "registration_endpoint": "https://access.stripe.com/mcp/oauth2/register",
    }

    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if url == "https://mcp.stripe.com/.well-known/oauth-protected-resource":
            return httpx.Response(
                200,
                json={
                    "resource": "https://mcp.stripe.com",
                    "authorization_servers": ["https://access.stripe.com/mcp"],
                },
            )
        if url == APPEND:
            return httpx.Response(404, text="Not Found")
        if url == INSERT:
            return httpx.Response(200, json=META)
        return httpx.Response(404, text="unexpected: " + url)

    h = HostedMcpDcrHandler(
        DcrConfig(
            plugin_id="stripe",
            discovery_url="https://mcp.stripe.com/.well-known/oauth-protected-resource",
        )
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        meta = await h._discover(client)
    assert meta["registration_endpoint"] == (
        "https://access.stripe.com/mcp/oauth2/register"
    )
    assert meta["authorization_endpoint"].endswith("/oauth2/authorize")
    # RFC 8707/9728: the protected-resource canonical URI is captured so the
    # authorize + token requests can carry the `resource` param (Stripe drops
    # you on the dashboard without it).
    assert h._discovered_resource == "https://mcp.stripe.com"
