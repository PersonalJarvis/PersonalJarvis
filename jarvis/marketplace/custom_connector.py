"""Custom connectors: any remote MCP server, added by its address.

A custom connector is the short way to a plugin. Instead of writing a
``plugin.json`` the owner types a name and the URL where an MCP server
accepts requests; this module asks that server how it wants to be signed in
to and turns the answer into an ordinary catalog entry:

* OAuth with Dynamic Client Registration -> ``hosted_mcp_oauth_dcr``, the
  same browser flow the shipped Notion and Linear entries use;
* a server that wants a key it cannot issue itself -> ``pat_paste``, checked
  with an MCP handshake instead of a REST "who am I" call;
* a server that answers without credentials -> ``hosted_mcp_open``.

The entry lands in the same override catalog an uploaded plugin uses, with
``source="local"``, so connect, disconnect, removal, the live tool registry,
the worker bridge and the composer's Add menu all treat it like any other
plugin. Nothing here keeps a second registry of its own.

Every request goes only to the address the owner typed. A redirect may not
leave that host, so a server cannot point the probe somewhere else, and plain
``http`` is accepted only for a server on this computer or the local network.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

import httpx

from jarvis.core.branding import PRODUCT_NAME
from jarvis.marketplace.catalog import PluginSpec

log = logging.getLogger(__name__)

ConnectorAuth = Literal["oauth", "token", "none"]
AuthChoice = Literal["auto", "oauth", "token", "none"]
Transport = Literal["http", "sse"]

#: The category every custom connector is filed under. Not part of
#: ``CATEGORY_ORDER``: the store appends categories it does not know.
CUSTOM_CATEGORY = "Custom"
#: The ``source`` an entry the owner added here carries (same as an upload).
LOCAL_SOURCE = "local"

_PROBE_TIMEOUT_S = 10.0
_MAX_REDIRECTS = 3
# Enough for an initialize result; an SSE reply that never ends is cut here.
_MAX_BODY_BYTES = 64 * 1024
_HEADER_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,63}$")
# Headers the transport owns. Letting a key ride in one of them would either
# be overwritten silently or break the request.
_RESERVED_HEADERS = frozenset(
    {
        "accept",
        "connection",
        "content-length",
        "content-type",
        "cookie",
        "host",
        "mcp-protocol-version",
        "mcp-session-id",
        "transfer-encoding",
    }
)
_LOCAL_SUFFIXES = (".localhost", ".local", ".lan", ".home.arpa", ".internal")
_PROTOCOL_VERSION = "2025-06-18"


class ConnectorError(ValueError):
    """A sentence for the owner: what is wrong with this address and why."""


@dataclass(frozen=True)
class ConnectorProbe:
    """What one handshake attempt learned about a server."""

    #: How the server wants to be signed in to, or ``none`` when it answered.
    auth: ConnectorAuth
    transport: Transport
    #: HTTP status of the decisive response.
    status: int
    #: RFC 9728 / RFC 8414 document the OAuth flow starts from.
    discovery_url: str | None = None
    #: False when the server signs in with OAuth but offers no Dynamic Client
    #: Registration, which a connector without a pre-registered client needs.
    registration: bool = True
    #: ``serverInfo.name`` from the initialize result, when the server answered.
    server_name: str | None = None


# ----------------------------------------------------------------------
# Address and identity
# ----------------------------------------------------------------------


def _is_local_host(host: str) -> bool:
    name = host.strip("[]").lower()
    if name == "localhost" or name.endswith(_LOCAL_SUFFIXES):
        return True
    try:
        ip = ipaddress.ip_address(name.split("%", 1)[0])
    except ValueError:
        # A single-label name ("nas", "homeassistant") only resolves on the
        # local network; anything with a dot is treated as public.
        return "." not in name
    return ip.is_loopback or ip.is_private or ip.is_link_local


def normalize_connector_url(raw: str) -> str:
    """The canonical form of an address the owner typed, or a ConnectorError."""
    text = (raw or "").strip()
    if not text:
        raise ConnectorError("Enter the address of the MCP server.")
    if "://" not in text:
        text = "https://" + text
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError as exc:
        raise ConnectorError("This is not a valid web address.") from exc
    scheme = parts.scheme.lower()
    if scheme not in ("https", "http"):
        raise ConnectorError("Use an https:// address.")
    if parts.username or parts.password:
        raise ConnectorError(
            "Leave the user name and password out of the address. "
            f"{PRODUCT_NAME} asks for sign-in details when you connect."
        )
    host = (parts.hostname or "").lower()
    if not host:
        raise ConnectorError("The address has no server name.")
    if scheme == "http" and not _is_local_host(host):
        raise ConnectorError(
            "Use an https:// address. Plain http is only accepted for a server "
            "on this computer or your local network."
        )
    netloc = f"[{host}]" if ":" in host else host
    if port is not None:
        netloc = f"{netloc}:{port}"
    return urlunsplit((scheme, netloc, parts.path or "/", parts.query, ""))


def _slug(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", folded.lower()).strip("-")
    return slug[:48].strip("-")


def connector_id(name: str, url: str, taken: Iterable[str]) -> str:
    """A catalog id for a new connector that collides with nothing in ``taken``.

    Built from the display name, or the server's host when the name holds no
    usable characters. Tool names become ``<id>/<tool>``, so the id stays in
    the lowercase ``a-z 0-9 -`` alphabet the rest of the catalog uses.
    """
    host = (urlsplit(url).hostname or "").removeprefix("www.").removeprefix("mcp.")
    base = _slug(name) or _slug(host.split(".")[0]) or "connector"
    used = set(taken)
    if base not in used:
        return base
    n = 2
    while f"{base}-{n}" in used:
        n += 1
    return f"{base}-{n}"


def display_name_for(name: str, url: str, probe: ConnectorProbe | None = None) -> str:
    """The name the owner typed, else the server's own, else its host."""
    typed = " ".join((name or "").split())[:80]
    if typed:
        return typed
    if probe is not None and probe.server_name:
        return probe.server_name.strip()[:80]
    host = (urlsplit(url).hostname or "connector").removeprefix("www.").removeprefix("mcp.")
    return host.split(".")[0].replace("-", " ").title() or "Connector"


def normalize_header_name(raw: str | None) -> str:
    """The header a token travels in; ``Authorization`` (Bearer) by default."""
    name = (raw or "").strip() or "Authorization"
    if not _HEADER_NAME_RE.fullmatch(name):
        raise ConnectorError("A header name may only hold letters, digits and dashes.")
    if name.lower() in _RESERVED_HEADERS:
        raise ConnectorError(f"{name} is set by the connection itself; choose another header.")
    return name


def _header_template(plugin_id: str, header_name: str) -> str:
    placeholder = f"${{plugin_{plugin_id}_access_token}}"
    if header_name.lower() == "authorization":
        return f"Authorization: Bearer {placeholder}"
    return f"{header_name}: {placeholder}"


# ----------------------------------------------------------------------
# Probe
# ----------------------------------------------------------------------


def _initialize_body() -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": _PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": PRODUCT_NAME, "version": "1.0"},
        },
    }


def _same_host_only(origin_host: str):
    """Response hook: a redirect may change the path, never the host."""

    async def _check(response: httpx.Response) -> None:
        if not response.has_redirect_location:
            return
        location = response.headers.get("location") or ""
        target = response.url.join(location)
        if (target.host or "").lower() != origin_host or (
            response.url.scheme == "https" and target.scheme != "https"
        ):
            raise ConnectorError(
                "The server redirected to a different address. Enter the final "
                "MCP address instead."
            )

    return _check


async def _read_limited(response: httpx.Response) -> bytes:
    """Body bytes up to the cap; an SSE reply stops after its first event."""
    chunks: list[bytes] = []
    size = 0
    streaming = "text/event-stream" in response.headers.get("content-type", "")
    async for chunk in response.aiter_bytes():
        chunks.append(chunk)
        size += len(chunk)
        if size >= _MAX_BODY_BYTES:
            break
        if streaming and b"\n\n" in b"".join(chunks).replace(b"\r\n", b"\n"):
            break
    return b"".join(chunks)[:_MAX_BODY_BYTES]


def _jsonrpc_payload(body: bytes, content_type: str) -> Mapping[str, Any] | None:
    """The JSON-RPC message in a JSON or SSE initialize reply, if there is one."""
    text = body.decode("utf-8", "replace")
    candidates: list[str] = []
    if "text/event-stream" in content_type:
        candidates = [
            line[5:].strip()
            for line in text.replace("\r\n", "\n").split("\n")
            if line.startswith("data:")
        ]
    else:
        candidates = [text]
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except ValueError:  # not JSON: try the next SSE data line
            continue
        if isinstance(parsed, Mapping) and parsed.get("jsonrpc") == "2.0":
            return parsed
    return None


def _resource_metadata_hint(www_authenticate: str) -> str | None:
    match = re.search(r'resource_metadata\s*=\s*"([^"]+)"', www_authenticate or "")
    return match.group(1) if match else None


def _well_known(url: str, suffix: str) -> list[str]:
    """RFC 9728/8414 locations for ``url``: path-inserted first, then the root."""
    parts = urlsplit(url)
    path = parts.path.rstrip("/")
    out = []
    if path:
        out.append(urlunsplit((parts.scheme, parts.netloc, f"/.well-known/{suffix}{path}", "", "")))
    out.append(urlunsplit((parts.scheme, parts.netloc, f"/.well-known/{suffix}", "", "")))
    return out


async def _get_json(client: httpx.AsyncClient, url: str) -> Mapping[str, Any] | None:
    try:
        response = await client.get(url, headers={"Accept": "application/json"})
    except ConnectorError:
        raise
    except httpx.HTTPError as exc:
        log.debug("custom connector: %s unreadable: %s", url, type(exc).__name__)
        return None
    if response.status_code != 200:
        return None
    try:
        parsed = response.json()
    except ValueError:  # a non-JSON answer is not an MCP endpoint
        return None
    return parsed if isinstance(parsed, Mapping) else None


def _is_auth_server(meta: Mapping[str, Any] | None) -> bool:
    return bool(
        meta
        and isinstance(meta.get("authorization_endpoint"), str)
        and isinstance(meta.get("token_endpoint"), str)
    )


async def _discover_oauth(
    client: httpx.AsyncClient, url: str, www_authenticate: str
) -> tuple[str, bool] | None:
    """``(discovery_url, has_registration)`` when the server signs in with OAuth.

    Follows the MCP authorization spec: the 401's ``resource_metadata`` hint
    first, then the protected-resource well-known locations, then the older
    servers that publish only authorization-server metadata on their host.
    The discovery URL handed back is exactly what ``HostedMcpDcrHandler``
    starts from, so the probe and the later sign-in read the same document.
    """
    from jarvis.marketplace.auth.oauth_dcr import _well_known_candidates

    host = (urlsplit(url).hostname or "").lower()
    candidates: list[str] = []
    hint = _resource_metadata_hint(www_authenticate)
    if hint and (urlsplit(hint).hostname or "").lower() == host:
        candidates.append(hint)
    candidates += [c for c in _well_known(url, "oauth-protected-resource") if c not in candidates]

    for candidate in candidates:
        meta = await _get_json(client, candidate)
        if meta is None:
            continue
        if _is_auth_server(meta):
            return candidate, bool(meta.get("registration_endpoint"))
        servers = meta.get("authorization_servers")
        if not isinstance(servers, list) or not servers or not isinstance(servers[0], str):
            continue
        for as_url in _well_known_candidates(servers[0]):
            as_meta = await _get_json(client, as_url)
            if _is_auth_server(as_meta):
                return candidate, bool(as_meta and as_meta.get("registration_endpoint"))
        # The resource names an auth server nobody can read: the sign-in flow
        # would fail the same way, so say OAuth without registration.
        return candidate, False

    for candidate in _well_known(url, "oauth-authorization-server"):
        meta = await _get_json(client, candidate)
        if _is_auth_server(meta):
            return candidate, bool(meta and meta.get("registration_endpoint"))
    return None


async def _auth_probe(
    client: httpx.AsyncClient, url: str, response: httpx.Response, transport: Transport
) -> ConnectorProbe:
    found = await _discover_oauth(client, url, response.headers.get("www-authenticate", ""))
    if found is None:
        return ConnectorProbe(auth="token", transport=transport, status=response.status_code)
    discovery_url, registration = found
    return ConnectorProbe(
        auth="oauth",
        transport=transport,
        status=response.status_code,
        discovery_url=discovery_url,
        registration=registration,
    )


async def _end_session(client: httpx.AsyncClient, url: str, response: httpx.Response) -> None:
    """Close the session a successful initialize opened. Best effort only."""
    session = response.headers.get("mcp-session-id")
    if not session:
        return
    try:
        await client.delete(url, headers={"Mcp-Session-Id": session})
    except (httpx.HTTPError, ConnectorError) as exc:
        # The server times the session out on its own; a probe never waits.
        log.debug("custom connector: session close skipped: %s", type(exc).__name__)


def _web_page() -> ConnectorError:
    return ConnectorError(
        "This address shows a web page, not an MCP server. Use the address the "
        "provider gives for MCP; it often ends in /mcp."
    )


def _unexpected_status(status: int) -> ConnectorError:
    return ConnectorError(
        f"The server answered HTTP {status}. Check that this is the address "
        "where it accepts MCP requests; it often ends in /mcp."
    )


async def probe_connector(
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ConnectorProbe:
    """Send one MCP ``initialize`` to ``url`` and report what the server wants.

    With ``headers`` the probe doubles as a credential check: a server that
    answers reports ``auth="none"``, one that refuses reports what it asks
    for instead. Raises ``ConnectorError`` with a readable sentence when the
    address is unreachable or does not speak MCP.
    """
    host = (urlsplit(url).hostname or "").lower()
    request_headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "User-Agent": f"{PRODUCT_NAME}/1.0",
        **dict(headers or {}),
    }
    try:
        async with httpx.AsyncClient(
            timeout=_PROBE_TIMEOUT_S,
            follow_redirects=True,
            max_redirects=_MAX_REDIRECTS,
            transport=transport,
            event_hooks={"response": [_same_host_only(host)]},
        ) as client:
            async with client.stream(
                "POST", url, json=_initialize_body(), headers=request_headers
            ) as response:
                status = response.status_code
                content_type = response.headers.get("content-type", "").lower()
                body = await _read_limited(response) if 200 <= status < 300 else b""
            if 200 <= status < 300:
                message = _jsonrpc_payload(body, content_type)
                if message is None:
                    if "text/html" in content_type:
                        raise _web_page()
                    raise ConnectorError("The server answered, but not with an MCP handshake.")
                await _end_session(client, url, response)
                if isinstance(message.get("error"), Mapping):
                    reason = str(message["error"].get("message", ""))[:200]
                    raise ConnectorError(f"The server refused the MCP handshake: {reason}")
                result = message.get("result")
                info = result.get("serverInfo") if isinstance(result, Mapping) else None
                name = info.get("name") if isinstance(info, Mapping) else None
                return ConnectorProbe(
                    auth="none",
                    transport="http",
                    status=status,
                    server_name=name if isinstance(name, str) and name.strip() else None,
                )
            if status in (401, 403):
                return await _auth_probe(client, url, response, "http")
            if status in (404, 405, 406):
                # Older servers speak the HTTP+SSE transport: the address is a
                # GET event stream, and POSTs go elsewhere.
                return await _probe_sse(client, url, dict(headers or {}))
            raise _unexpected_status(status)
    except ConnectorError:
        raise
    except httpx.TooManyRedirects as exc:
        raise ConnectorError("The server redirects in a loop.") from exc
    except httpx.TimeoutException as exc:
        seconds = int(_PROBE_TIMEOUT_S)
        raise ConnectorError(f"{host} did not answer within {seconds} seconds.") from exc
    except httpx.HTTPError as exc:
        raise ConnectorError(
            f"{PRODUCT_NAME} could not reach {host}. Check the address and your connection."
        ) from exc


async def _probe_sse(
    client: httpx.AsyncClient, url: str, headers: dict[str, str]
) -> ConnectorProbe:
    async with client.stream(
        "GET",
        url,
        headers={"Accept": "text/event-stream", "User-Agent": f"{PRODUCT_NAME}/1.0", **headers},
    ) as response:
        status = response.status_code
        content_type = response.headers.get("content-type", "").lower()
    if status == 200 and "text/event-stream" in content_type:
        return ConnectorProbe(auth="none", transport="sse", status=status)
    if status in (401, 403):
        return await _auth_probe(client, url, response, "sse")
    if status == 200 and "text/html" in content_type:
        raise _web_page()
    raise _unexpected_status(status)


# ----------------------------------------------------------------------
# Decision and catalog entry
# ----------------------------------------------------------------------


def resolve_auth(choice: AuthChoice, probe: ConnectorProbe) -> ConnectorAuth:
    """The sign-in the connector will use, given the owner's pick and the probe."""
    if choice == "auto":
        if probe.auth == "oauth" and not probe.registration:
            raise ConnectorError(
                "This server signs in with OAuth but does not let new apps register "
                "themselves, so a custom connector cannot complete its sign-in. If the "
                "provider offers an API key or access token, choose \"Access token\" "
                "under Advanced settings."
            )
        return probe.auth
    if choice == "oauth":
        if probe.auth == "none":
            raise ConnectorError(
                "This server answered without any sign-in, so there is no OAuth "
                "login to start. Choose \"Detect automatically\"."
            )
        if probe.auth != "oauth":
            raise ConnectorError("This server publishes no OAuth sign-in. Try \"Access token\".")
        if not probe.registration:
            return resolve_auth("auto", probe)
        return "oauth"
    if choice == "none" and probe.auth != "none":
        raise ConnectorError(
            "This server asks for sign-in before it answers. Choose \"Detect "
            "automatically\" or \"Access token\"."
        )
    return choice


def build_connector_spec(
    *,
    plugin_id: str,
    display_name: str,
    url: str,
    auth: ConnectorAuth,
    probe: ConnectorProbe,
    header_name: str = "Authorization",
) -> PluginSpec:
    """The catalog entry for a custom connector."""
    host = urlsplit(url).hostname or url
    mcp_server: dict[str, Any] = {"transport": probe.transport, "url": url}
    if auth == "oauth":
        auth_block: dict[str, Any] = {
            "mode": "hosted_mcp_oauth_dcr",
            "discovery_url": probe.discovery_url,
            "mcp_url": url,
            "refresh_supported": True,
        }
        mcp_server["auth_header_template"] = _header_template(plugin_id, "Authorization")
        longevity = "self_renewing"
    elif auth == "token":
        header_label = (
            "Authorization header (as a Bearer token)"
            if header_name.lower() == "authorization"
            else f"{header_name} header"
        )
        auth_block = {
            "mode": "pat_paste",
            # No page is known that issues the key; the dialog hides the link.
            "token_creation_url": "",
            "token_prefix": "",
            "validation_endpoint": url,
            "validation_method": "mcp_initialize",
            "instruction_md": (
                f"Use the API key or access token the provider of {display_name} "
                f"gave you. {PRODUCT_NAME} sends it in the {header_label}."
            ),
        }
        mcp_server["auth_header_template"] = _header_template(plugin_id, header_name)
        longevity = "permanent"
    else:
        auth_block = {"mode": "hosted_mcp_open", "mcp_url": url}
        longevity = "permanent"
    return PluginSpec.model_validate(
        {
            "id": plugin_id,
            "display_name": display_name,
            "description": f"MCP server at {host}",
            "category": CUSTOM_CATEGORY,
            "source": LOCAL_SOURCE,
            "logo_slug": "",
            "featured": False,
            "longevity": longevity,
            "auth": auth_block,
            "mcp_server": mcp_server,
        }
    )


def connector_url(spec: PluginSpec) -> str | None:
    """The server address of a catalog entry, for duplicate checks."""
    mcp = spec.mcp_server or {}
    url = mcp.get("url")
    return str(url) if isinstance(url, str) and url else None


def connector_auth_headers(spec: PluginSpec, token: str) -> dict[str, str]:
    """The header a pasted token travels in, built from the entry's template."""
    from jarvis.marketplace.mcp_bridge import _resolve_placeholders, _token_replacements

    template = (spec.mcp_server or {}).get("auth_header_template")
    if not template:
        return {"Authorization": f"Bearer {token}"}
    resolved = _resolve_placeholders(str(template), _token_replacements(spec.id, token))
    key, sep, value = resolved.partition(":")
    return {key.strip(): value.strip()} if sep else {}


__all__ = [
    "CUSTOM_CATEGORY",
    "AuthChoice",
    "ConnectorAuth",
    "ConnectorError",
    "ConnectorProbe",
    "build_connector_spec",
    "connector_auth_headers",
    "connector_id",
    "connector_url",
    "display_name_for",
    "normalize_connector_url",
    "normalize_header_name",
    "probe_connector",
    "resolve_auth",
]
