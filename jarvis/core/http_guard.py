"""Keep an https-only download https-only, redirect after redirect.

Several places fetch a URL that came from the community registry: the index
itself, a skill's ``SKILL.md``, a wallpaper's bytes, a plugin's files. Each of
them checks the URL is ``https://`` before fetching, and each says why in a
comment — the fetch runs on the user's machine, so a plaintext or internal
address would let a published entry aim the backend at the loopback API, the
router's admin page, or a cloud metadata endpoint.

That check covers the FIRST url only. With ``follow_redirects=True`` the
server on the other end picks the next one, and a publisher who controls
``https://their-host/skill.md`` controls its ``Location`` header too. So the
guard has to travel with the request rather than stand in front of it.

Usage — one keyword argument on the client::

    async with httpx.AsyncClient(follow_redirects=True, **https_only_async()) as c:
        resp = await c.get(url)

    with httpx.Client(follow_redirects=True, **https_only()) as c:
        resp = c.get(url)

A redirect to anything but https raises :class:`InsecureRedirect`, which is an
``httpx.HTTPError`` — the exception every one of these call sites already
handles as "the download failed".
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from typing import Any

import httpx

__all__ = [
    "BlockedDestination",
    "InsecureRedirect",
    "https_only",
    "https_only_async",
    "public_only_async",
]


class InsecureRedirect(httpx.HTTPError):
    """A redirect tried to leave https.

    Subclasses ``httpx.HTTPError`` on purpose: every download this guards is
    already wrapped in ``except httpx.HTTPError``, so a refused redirect
    surfaces as the ordinary "could not fetch" the caller knows how to report.
    """


def _check(response: httpx.Response) -> None:
    if not response.has_redirect_location:
        return
    location = response.headers.get("location")
    if not location:
        return
    # Resolved against the current URL: a relative Location keeps the scheme it
    # already had, and only an absolute one can change it.
    target = response.url.join(location)
    if target.scheme != "https":
        raise InsecureRedirect(f"refusing a non-https redirect from {response.url} to {target}")


async def _check_async(response: httpx.Response) -> None:
    _check(response)


def https_only() -> dict[str, Any]:
    """Client kwargs that refuse any redirect leaving https (sync client)."""
    return {"event_hooks": {"response": [_check]}}


def https_only_async() -> dict[str, Any]:
    """The same, for ``httpx.AsyncClient`` — its hooks must be awaitable."""
    return {"event_hooks": {"response": [_check_async]}}


# ---------------------------------------------------------------------------
# Public-destination guard: for a URL the USER pasted (not a registry entry),
# plain http is fine, but the fetch runs server-side, so it must never reach
# the loopback API, the LAN, or a cloud metadata endpoint. It runs as a
# REQUEST hook, so every redirect hop is checked before it is sent.
# ---------------------------------------------------------------------------


class BlockedDestination(httpx.HTTPError):
    """The request targeted a non-public address or a disallowed scheme.

    An ``httpx.HTTPError`` for the same reason as :class:`InsecureRedirect`:
    call sites already report that as "the download failed".
    """


def _is_public_ip(value: str) -> bool:
    ip = ipaddress.ip_address(value.split("%", 1)[0])  # drop an IPv6 zone id
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


async def _check_public_request(request: httpx.Request, schemes: frozenset[str]) -> None:
    url = request.url
    if url.scheme not in schemes:
        raise BlockedDestination(f"refusing a {url.scheme!r} URL")
    host = url.host
    if not host:
        raise BlockedDestination("refusing a URL without a host")
    try:
        # A literal address needs no lookup.
        literal_ok = _is_public_ip(host)
    except ValueError:  # not an IP literal, so it is a hostname and DNS resolution below decides
        literal_ok = None
    if literal_ok is not None:
        if not literal_ok:
            raise BlockedDestination(f"refusing non-public address {host}")
        return
    port = url.port or (443 if url.scheme == "https" else 80)
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise BlockedDestination(f"cannot resolve {host}") from exc
    addresses = {info[4][0] for info in infos}
    # Every address must be public: a name that resolves to both a public and a
    # private address could otherwise be connected to on the private one.
    if not addresses or not all(_is_public_ip(str(a)) for a in addresses):
        raise BlockedDestination(f"refusing {host}: it resolves to a non-public address")


def public_only_async(*, schemes: tuple[str, ...] = ("https",)) -> dict[str, Any]:
    """``httpx.AsyncClient`` kwargs that refuse non-public destinations.

    Every request — the first and each redirect hop — must use one of
    ``schemes`` and resolve only to globally routable addresses. The lookup
    happens just before httpx connects; this narrows but does not fully close a
    DNS-rebinding window, which is acceptable for a user-initiated download.
    """
    allowed = frozenset(s.lower() for s in schemes)

    async def _hook(request: httpx.Request) -> None:
        await _check_public_request(request, allowed)

    return {"event_hooks": {"request": [_hook]}}
