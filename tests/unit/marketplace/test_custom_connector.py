"""Custom connectors: an MCP server added by name and address.

The probe is the whole inspection, so these tests pin what it concludes from
each answer a real server can give: a handshake (no sign-in), a 401 that
points at OAuth metadata, a 401 with nothing to discover (a key is needed),
an old SSE endpoint, and the answers that mean "this is not an MCP address".
Every request runs against an ``httpx.MockTransport``; nothing leaves the box.
"""

from __future__ import annotations

import json

import httpx
import pytest

from jarvis.marketplace.catalog import PluginSpec
from jarvis.marketplace.custom_connector import (
    ConnectorError,
    ConnectorProbe,
    build_connector_spec,
    connector_auth_headers,
    connector_id,
    display_name_for,
    normalize_connector_url,
    normalize_header_name,
    probe_connector,
    resolve_auth,
)
from jarvis.marketplace.plugin_mcp import plugin_to_mcp_server_spec
from jarvis.marketplace.token_store import Tokens

URL = "https://mcp.example.com/mcp"


def _initialize_reply(name: str = "Example Server") -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "protocolVersion": "2025-06-18",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": name, "version": "1.0.0"},
        },
    }


def _transport(routes: dict[tuple[str, str], httpx.Response], seen: list | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        key = (request.method, str(request.url))
        if key in routes:
            return routes[key]
        return httpx.Response(404)

    return httpx.MockTransport(handler)


# ----------------------------------------------------------------------
# Address, id and header rules
# ----------------------------------------------------------------------


def test_a_bare_host_becomes_an_https_address() -> None:
    assert normalize_connector_url("mcp.example.com/mcp") == URL


def test_the_fragment_is_dropped_and_the_host_lowercased() -> None:
    assert normalize_connector_url(" https://MCP.Example.com/mcp#x ") == URL


@pytest.mark.parametrize(
    "url",
    ["http://localhost:8000/mcp", "http://127.0.0.1:3000/mcp", "http://192.168.1.20/mcp",
     "http://nas/mcp", "http://homeassistant.local:8123/mcp_server/sse"],
)
def test_plain_http_is_accepted_on_this_computer_and_the_local_network(url: str) -> None:
    assert normalize_connector_url(url).startswith("http://")


@pytest.mark.parametrize(
    ("url", "fragment"),
    [
        ("", "Enter the address"),
        ("http://mcp.example.com/mcp", "https://"),
        ("ftp://mcp.example.com", "https://"),
        ("https://user:secret@mcp.example.com/mcp", "user name and password"),
    ],
)
def test_addresses_that_cannot_be_used_are_refused_with_a_reason(url: str, fragment: str) -> None:
    with pytest.raises(ConnectorError, match=fragment):
        normalize_connector_url(url)


def test_the_id_comes_from_the_name_and_never_collides() -> None:
    assert connector_id("Linear Tools", URL, []) == "linear-tools"
    assert connector_id("Linear Tools", URL, ["linear-tools"]) == "linear-tools-2"
    assert connector_id("Linear Tools", URL, ["linear-tools", "linear-tools-2"]) == "linear-tools-3"


def test_an_id_from_a_name_without_ascii_falls_back_to_the_host() -> None:
    assert connector_id("Café Tools", URL, []) == "cafe-tools"
    assert connector_id("日本", "https://mcp.acme.io/mcp", []) == "acme"


def test_a_blank_name_uses_the_servers_own_name() -> None:
    probe = ConnectorProbe(auth="none", transport="http", status=200, server_name="Acme MCP")
    assert display_name_for("  ", URL, probe) == "Acme MCP"
    assert display_name_for("", "https://mcp.acme.io/mcp") == "Acme"


def test_header_names_default_to_authorization_and_refuse_transport_headers() -> None:
    assert normalize_header_name(None) == "Authorization"
    assert normalize_header_name("X-API-Key") == "X-API-Key"
    with pytest.raises(ConnectorError):
        normalize_header_name("Content-Type")
    with pytest.raises(ConnectorError):
        normalize_header_name("X API Key")


# ----------------------------------------------------------------------
# Probe
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_server_that_answers_needs_no_sign_in() -> None:
    transport = _transport({("POST", URL): httpx.Response(200, json=_initialize_reply())})

    probe = await probe_connector(URL, transport=transport)

    assert probe.auth == "none"
    assert probe.transport == "http"
    assert probe.server_name == "Example Server"


@pytest.mark.asyncio
async def test_an_sse_formatted_handshake_is_read_from_its_first_event() -> None:
    body = f"event: message\ndata: {json.dumps(_initialize_reply('Streamer'))}\n\n"
    reply = httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})
    probe = await probe_connector(URL, transport=_transport({("POST", URL): reply}))

    assert probe.auth == "none"
    assert probe.server_name == "Streamer"


@pytest.mark.asyncio
async def test_the_session_a_probe_opened_is_closed_again() -> None:
    seen: list[httpx.Request] = []
    reply = httpx.Response(200, json=_initialize_reply(), headers={"mcp-session-id": "s-1"})
    transport = _transport(
        {("POST", URL): reply, ("DELETE", URL): httpx.Response(204)}, seen
    )

    await probe_connector(URL, transport=transport)

    closing = [r for r in seen if r.method == "DELETE"]
    assert closing and closing[0].headers["mcp-session-id"] == "s-1"


@pytest.mark.asyncio
async def test_a_401_with_resource_metadata_and_registration_is_oauth() -> None:
    resource = "https://mcp.example.com/.well-known/oauth-protected-resource/mcp"
    transport = _transport(
        {
            ("POST", URL): httpx.Response(
                401, headers={"www-authenticate": f'Bearer resource_metadata="{resource}"'}
            ),
            ("GET", resource): httpx.Response(
                200,
                json={"resource": URL, "authorization_servers": ["https://auth.example.com"]},
            ),
            ("GET", "https://auth.example.com/.well-known/oauth-authorization-server"):
                httpx.Response(
                    200,
                    json={
                        "authorization_endpoint": "https://auth.example.com/authorize",
                        "token_endpoint": "https://auth.example.com/token",
                        "registration_endpoint": "https://auth.example.com/register",
                    },
                ),
        }
    )

    probe = await probe_connector(URL, transport=transport)

    assert probe.auth == "oauth"
    assert probe.discovery_url == resource
    assert probe.registration is True
    assert resolve_auth("auto", probe) == "oauth"


@pytest.mark.asyncio
async def test_oauth_without_self_registration_is_named_not_attempted() -> None:
    root_meta = "https://mcp.example.com/.well-known/oauth-authorization-server"
    transport = _transport(
        {
            ("POST", URL): httpx.Response(401),
            ("GET", root_meta): httpx.Response(
                200,
                json={
                    "authorization_endpoint": "https://mcp.example.com/authorize",
                    "token_endpoint": "https://mcp.example.com/token",
                },
            ),
        }
    )

    probe = await probe_connector(URL, transport=transport)

    assert probe.auth == "oauth"
    assert probe.registration is False
    with pytest.raises(ConnectorError, match="Access token"):
        resolve_auth("auto", probe)
    # The owner's own pick of a token stays possible.
    assert resolve_auth("token", probe) == "token"


@pytest.mark.asyncio
async def test_a_401_with_nothing_to_discover_needs_a_token() -> None:
    probe = await probe_connector(URL, transport=_transport({("POST", URL): httpx.Response(401)}))

    assert probe.auth == "token"
    assert resolve_auth("auto", probe) == "token"


@pytest.mark.asyncio
async def test_a_token_in_the_headers_turns_the_probe_into_a_credential_check() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.headers.get("x-api-key") == "good":
            return httpx.Response(200, json=_initialize_reply())
        return httpx.Response(401)

    transport = httpx.MockTransport(handler)

    accepted = await probe_connector(URL, headers={"X-API-Key": "good"}, transport=transport)
    refused = await probe_connector(URL, headers={"X-API-Key": "bad"}, transport=transport)

    assert accepted.auth == "none"
    assert refused.auth == "token"


@pytest.mark.asyncio
async def test_an_old_sse_endpoint_is_found_behind_a_405() -> None:
    sse = "https://mcp.example.com/sse"
    transport = _transport(
        {
            ("POST", sse): httpx.Response(405),
            ("GET", sse): httpx.Response(
                200, text="event: endpoint\ndata: /messages\n\n",
                headers={"content-type": "text/event-stream"},
            ),
        }
    )

    probe = await probe_connector(sse, transport=transport)

    assert probe.transport == "sse"
    assert probe.auth == "none"


@pytest.mark.asyncio
async def test_a_web_page_is_not_an_mcp_server() -> None:
    page = httpx.Response(200, text="<html></html>", headers={"content-type": "text/html"})
    with pytest.raises(ConnectorError, match="web page"):
        await probe_connector(URL, transport=_transport({("POST", URL): page}))


@pytest.mark.asyncio
async def test_a_web_page_that_refuses_post_is_still_named_a_web_page() -> None:
    page = httpx.Response(200, text="<html></html>", headers={"content-type": "text/html"})
    transport = _transport({("POST", URL): httpx.Response(405), ("GET", URL): page})
    with pytest.raises(ConnectorError, match="web page"):
        await probe_connector(URL, transport=transport)


@pytest.mark.asyncio
async def test_an_unexpected_status_names_the_likely_fix() -> None:
    with pytest.raises(ConnectorError, match="/mcp"):
        await probe_connector(URL, transport=_transport({("POST", URL): httpx.Response(500)}))


@pytest.mark.asyncio
async def test_a_redirect_to_another_host_is_refused() -> None:
    moved = httpx.Response(307, headers={"location": "http://169.254.169.254/latest"})
    with pytest.raises(ConnectorError, match="different address"):
        await probe_connector(URL, transport=_transport({("POST", URL): moved}))


@pytest.mark.asyncio
async def test_an_unreachable_server_is_a_readable_sentence() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(ConnectorError, match="could not reach mcp.example.com"):
        await probe_connector(URL, transport=httpx.MockTransport(handler))


# ----------------------------------------------------------------------
# Decision and catalog entry
# ----------------------------------------------------------------------


def test_the_owner_cannot_force_a_sign_in_the_server_does_not_have() -> None:
    answered = ConnectorProbe(auth="none", transport="http", status=200)
    wants_key = ConnectorProbe(auth="token", transport="http", status=401)
    with pytest.raises(ConnectorError):
        resolve_auth("oauth", answered)
    with pytest.raises(ConnectorError):
        resolve_auth("oauth", wants_key)
    with pytest.raises(ConnectorError):
        resolve_auth("none", wants_key)
    assert resolve_auth("token", answered) == "token"


def _oauth_probe() -> ConnectorProbe:
    return ConnectorProbe(
        auth="oauth",
        transport="http",
        status=401,
        discovery_url="https://mcp.example.com/.well-known/oauth-protected-resource",
    )


def test_an_oauth_connector_uses_the_dcr_flow_and_a_bearer_header() -> None:
    spec = build_connector_spec(
        plugin_id="acme", display_name="Acme", url=URL, auth="oauth", probe=_oauth_probe()
    )

    assert isinstance(spec, PluginSpec)
    assert spec.source == "local"
    assert spec.category == "Custom"
    assert spec.auth.mode == "hosted_mcp_oauth_dcr"
    assert spec.auth.discovery_url == _oauth_probe().discovery_url
    resolved = plugin_to_mcp_server_spec(spec, Tokens(access="tok"))
    assert resolved is not None
    assert resolved[0].url == URL
    assert resolved[0].headers == {"Authorization": "Bearer tok"}


def test_a_token_connector_sends_the_key_in_the_chosen_header() -> None:
    probe = ConnectorProbe(auth="token", transport="http", status=401)
    spec = build_connector_spec(
        plugin_id="acme", display_name="Acme", url=URL, auth="token", probe=probe,
        header_name="X-API-Key",
    )

    assert spec.auth.mode == "pat_paste"
    assert spec.auth.validation_method == "mcp_initialize"
    assert spec.auth.validation_endpoint == URL
    assert connector_auth_headers(spec, "k-1") == {"X-API-Key": "k-1"}
    resolved = plugin_to_mcp_server_spec(spec, Tokens(access="k-1"))
    assert resolved is not None and resolved[0].headers == {"X-API-Key": "k-1"}


def test_an_open_connector_sends_no_credential_and_keeps_its_transport() -> None:
    probe = ConnectorProbe(auth="none", transport="sse", status=200)
    spec = build_connector_spec(
        plugin_id="acme", display_name="Acme", url=URL, auth="none", probe=probe
    )

    assert spec.auth.mode == "hosted_mcp_open"
    resolved = plugin_to_mcp_server_spec(spec, Tokens(access="open"))
    assert resolved is not None
    assert resolved[0].transport == "sse"
    assert resolved[0].headers == {}
