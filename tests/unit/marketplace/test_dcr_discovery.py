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


def test_resource_scopes_do_not_request_issuer_admin_or_billing_access():
    handler = HostedMcpDcrHandler(DcrConfig("todoist", "https://example.test/resource"))
    handler._resource_scopes = ["data:read_write"]
    assert handler._scopes_from_meta(
        {"scopes_supported": ["data:read_write", "billing:read_write", "dev:app_console"]}
    ) == "data:read_write"


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


@pytest.mark.asyncio
async def test_discover_accepts_auth_server_metadata_as_discovery_url():
    """Servers without a protected-resource document (Atlassian) are configured
    with the RFC 8414 metadata URL itself; no resource indicator is sent."""
    URL = "https://mcp.atlassian.com/.well-known/oauth-authorization-server"
    META = {
        "issuer": "https://mcp.atlassian.com",
        "authorization_endpoint": "https://mcp.atlassian.com/v1/authorize",
        "token_endpoint": "https://cf.mcp.atlassian.com/v1/token",
        "registration_endpoint": "https://mcp.atlassian.com/v1/register",
    }
    requested: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        requested.append(str(req.url))
        if str(req.url) == URL:
            return httpx.Response(200, json=META)
        return httpx.Response(404, text="unexpected: " + str(req.url))

    h = HostedMcpDcrHandler(DcrConfig(plugin_id="atlassian", discovery_url=URL))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        meta = await h._discover(client)
    assert meta["registration_endpoint"] == "https://mcp.atlassian.com/v1/register"
    assert requested == [URL]
    assert h._discovered_resource is None


@pytest.mark.asyncio
async def test_discover_still_rejects_a_document_with_no_auth_server():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"resource": "https://example.test/mcp"})

    h = HostedMcpDcrHandler(
        DcrConfig(plugin_id="x", discovery_url="https://example.test/.well-known/x")
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError, match="no authorization_servers"):
            await h._discover(client)
