"""The https-only redirect guard.

The registry is auto-merged, so a published entry's URL is attacker-chosen and
so is the ``Location`` header of the host it points at. Checking the first URL
is https — which every one of these call sites does — proves nothing about the
second one.
"""

from __future__ import annotations

import httpx
import pytest

from jarvis.core.http_guard import (
    BlockedDestination,
    InsecureRedirect,
    https_only,
    https_only_async,
    public_only_async,
)


def _redirecting(target: str) -> httpx.MockTransport:
    """https://start.example redirects once, to ``target``."""

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "https://start.example/skill.md":
            return httpx.Response(302, headers={"location": target})
        return httpx.Response(200, text="landed")

    return httpx.MockTransport(handler)


@pytest.mark.parametrize(
    "target",
    [
        "http://127.0.0.1:8765/api/anything",  # the user's own Jarvis
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata
        "http://192.168.1.1/admin",  # the router on the LAN
    ],
)
@pytest.mark.asyncio
async def test_redirect_off_https_is_refused(target: str) -> None:
    async with httpx.AsyncClient(
        transport=_redirecting(target), follow_redirects=True, **https_only_async()
    ) as client:
        with pytest.raises(InsecureRedirect):
            await client.get("https://start.example/skill.md")


@pytest.mark.asyncio
async def test_refusal_is_an_http_error() -> None:
    """Every caller already handles httpx.HTTPError as "the download failed"."""
    async with httpx.AsyncClient(
        transport=_redirecting("http://127.0.0.1:8765/"),
        follow_redirects=True,
        **https_only_async(),
    ) as client:
        with pytest.raises(httpx.HTTPError):
            await client.get("https://start.example/skill.md")


@pytest.mark.asyncio
async def test_https_redirect_still_follows() -> None:
    """A CDN moving a file is the normal case and must keep working."""
    async with httpx.AsyncClient(
        transport=_redirecting("https://cdn.example/skill.md"),
        follow_redirects=True,
        **https_only_async(),
    ) as client:
        resp = await client.get("https://start.example/skill.md")
    assert resp.status_code == 200
    assert resp.text == "landed"


def test_sync_client_guard_refuses_too() -> None:
    with httpx.Client(
        transport=_redirecting("http://127.0.0.1:8765/"),
        follow_redirects=True,
        **https_only(),
    ) as client:
        with pytest.raises(InsecureRedirect):
            client.get("https://start.example/skill.md")


# --------------------------------------------------------------------------
# public_only_async — a user-pasted URL may be http, but never non-public
# --------------------------------------------------------------------------


def _public_redirecting(target: str) -> httpx.MockTransport:
    """http://8.8.8.8 (a literal public address, no DNS) redirects to target."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "8.8.8.8":
            return httpx.Response(302, headers={"location": target})
        return httpx.Response(200, text="landed")

    return httpx.MockTransport(handler)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8765/api/anything",
        "http://localhost:8765/api/anything",
        "http://169.254.169.254/latest/meta-data/",
        "http://192.168.1.1/admin",
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",
    ],
)
@pytest.mark.asyncio
async def test_public_only_refuses_non_public_first_hop(url: str) -> None:
    async with httpx.AsyncClient(
        transport=_public_redirecting("https://1.1.1.1/x"),
        **public_only_async(schemes=("http", "https")),
    ) as client:
        with pytest.raises(BlockedDestination):
            await client.get(url)


@pytest.mark.asyncio
async def test_public_only_refuses_a_redirect_into_the_lan() -> None:
    async with httpx.AsyncClient(
        transport=_public_redirecting("http://10.0.0.5/secret"),
        follow_redirects=True,
        **public_only_async(schemes=("http", "https")),
    ) as client:
        with pytest.raises(httpx.HTTPError):
            await client.get("http://8.8.8.8/skill.md")


@pytest.mark.asyncio
async def test_public_only_follows_a_public_redirect() -> None:
    async with httpx.AsyncClient(
        transport=_public_redirecting("https://1.1.1.1/skill.md"),
        follow_redirects=True,
        **public_only_async(schemes=("http", "https")),
    ) as client:
        resp = await client.get("http://8.8.8.8/skill.md")
    assert resp.text == "landed"


@pytest.mark.asyncio
async def test_public_only_default_is_https_only() -> None:
    async with httpx.AsyncClient(
        transport=_public_redirecting("https://1.1.1.1/x"), **public_only_async()
    ) as client:
        with pytest.raises(BlockedDestination):
            await client.get("http://8.8.8.8/skill.md")
