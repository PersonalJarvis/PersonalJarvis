"""Adversarial network policies and cancellation with bounded transport fakes."""

from __future__ import annotations

import asyncio
import gzip
import zlib
import socket

import aiohttp
import pytest

from jarvis.swarm.egress import (
    EgressClient,
    EgressDenied,
    PublicResolver,
    public_address,
    validate_url,
)


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "169.254.169.254",
        "100.100.100.200",
        "0.0.0.0",  # noqa: S104 - adversarial destination, never a server bind
        "168.63.129.16",
        "192.0.0.8",
        "192.168.1.1",
        "172.16.0.1",
        "224.0.0.1",
        "::1",
        "fe80::1",
        "fc00::1",
        "::ffff:127.0.0.1",
        "64:ff9b::7f00:1",
        "2002:7f00:1::",
        "fe80::1%3",
    ],
)
def test_private_special_and_translated_addresses_denied(address: str) -> None:
    with pytest.raises(EgressDenied):
        public_address(address)


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://localhost",
        "http://private.local",
        "http://service.internal",
        "https://user:password@example.com",
        "http://127.0.0.1",
        "http://[::1]",
        "http://example.com:8080",
        "https://example.com\\@127.0.0.1",
        "https://example.com\n",
        "http://2130706433",
        "http://metadata.google.internal",
        "http://[fe80::1%25eth0]",
    ],
)
def test_unsafe_url_forms_denied(url: str) -> None:
    with pytest.raises(EgressDenied):
        validate_url(url)


def test_allowlist_does_not_accept_suffix_spoofing() -> None:
    assert validate_url("https://api.example.com/value", ["example.com"]).host == "api.example.com"
    with pytest.raises(EgressDenied):
        validate_url("https://example.com.attacker.net", ["example.com"])
    with pytest.raises(EgressDenied):
        validate_url("https://example.com", ["*"])


@pytest.mark.asyncio
async def test_resolver_pins_validated_addresses_and_rejects_rebinding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answers = ["93.184.216.34", "127.0.0.1"]

    async def lookup(host, port, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (answers.pop(0), port))]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", lookup)
    resolver = PublicResolver()
    first = await resolver.resolve("example.com", 443)
    assert first[0]["host"] == "93.184.216.34"
    assert first[0]["flags"] == socket.AI_NUMERICHOST
    with pytest.raises(EgressDenied):
        await resolver.resolve("example.com", 443)


@pytest.mark.asyncio
async def test_mixed_public_private_dns_answers_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def lookup(host, port, **kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))
            for ip in ("93.184.216.34", "10.0.0.1")
        ]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", lookup)
    with pytest.raises(EgressDenied):
        await PublicResolver().resolve("example.com", 443)


class Content:
    def __init__(self, chunks: tuple[bytes, ...], *, stall: bool = False) -> None:
        self.chunks = chunks
        self.stall = stall

    async def iter_chunked(self, size):
        if self.stall:
            await asyncio.Event().wait()
        for chunk in self.chunks:
            yield chunk


class Response:
    def __init__(self, *, status=200, headers=None, chunks=(b"data",), length=None, stall=False):
        self.status = status
        self.headers = headers or {}
        self.content_length = length
        self.content = Content(chunks, stall=stall)
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.close()

    def close(self):
        self.closed = True


class Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.urls = []
        self.closed = False

    def get(self, url, **kwargs):
        assert kwargs["allow_redirects"] is False
        self.urls.append(str(url))
        return self.responses.pop(0)

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_session_has_no_cookies_proxies_or_host_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://user:secret@127.0.0.1:9")
    client = EgressClient()
    try:
        session = client._client()
        assert not session.trust_env
        assert isinstance(session.cookie_jar, aiohttp.DummyCookieJar)
        assert session.auth is None
        assert session.headers.get("Authorization") is None
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "location", ["http://127.0.0.1/secret", "https://other.net", "https://user:secret@example.com"]
)
async def test_redirects_revalidate_scope_before_connecting(location: str) -> None:
    client = EgressClient()
    response = Response(status=302, headers={"Location": location})
    session = Session([response])
    client._session = session
    with pytest.raises(EgressDenied):
        await client.fetch("https://example.com", allowed_domains=["example.com"])
    assert len(session.urls) == 1
    assert response.closed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        Response(length=20),
        Response(chunks=(b"a" * 8, b"b" * 8)),
        Response(headers={"Content-Encoding": "gzip"}),
    ],
)
async def test_body_limits_and_compression_bombs_fail_closed(response: Response) -> None:
    client = EgressClient()
    client._session = Session([response])
    with pytest.raises(EgressDenied):
        await client.fetch("https://example.com", max_bytes=10)
    assert response.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("encoding,compress", [("gzip", gzip.compress), ("deflate", zlib.compress)])
async def test_compressed_sources_decode_with_both_wire_and_output_bounds(encoding, compress):
    body = b"Official source content. " * 100
    wire = compress(body)
    response = Response(
        headers={"Content-Encoding": encoding},
        chunks=tuple(wire[index : index + 3] for index in range(0, len(wire), 3)),
    )
    client = EgressClient()
    client._session = Session([response])
    result = await client.fetch("https://example.com", max_bytes=len(body))
    assert result.body == body
    assert response.closed
    bomb = Response(headers={"Content-Encoding": encoding}, chunks=(compress(b"x" * 1000000),))
    client._session = Session([bomb])
    with pytest.raises(EgressDenied):
        await client.fetch("https://example.com", max_bytes=10000)
    assert bomb.closed


@pytest.mark.asyncio
async def test_truncated_and_trailing_compression_data_are_rejected():
    for wire in (gzip.compress(b"source")[:-2], gzip.compress(b"source") + b"trailing"):
        response = Response(headers={"Content-Encoding": "gzip"}, chunks=(wire,))
        client = EgressClient()
        client._session = Session([response])
        with pytest.raises(EgressDenied):
            await client.fetch("https://example.com", max_bytes=1000)
        assert response.closed


@pytest.mark.asyncio
async def test_cancel_releases_transport_and_timeout_covers_waiting() -> None:
    client = EgressClient()
    response = Response(stall=True)
    client._session = Session([response])
    with pytest.raises(TimeoutError):
        await client.fetch("https://example.com", timeout_s=0.01)
    assert response.closed
    with pytest.raises(asyncio.CancelledError):
        await client.fetch("https://example.com", cancel=lambda: True)


@pytest.mark.asyncio
async def test_public_response_has_integrity_and_exact_bytes() -> None:
    client = EgressClient()
    client._session = Session([Response(chunks=(b"hello",))])
    result = await client.fetch("https://example.com")
    assert result.body == b"hello"
    assert result.sha256 == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
