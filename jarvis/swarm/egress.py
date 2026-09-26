"""Public HTTP retrieval through a bounded, credential-free egress broker.

The resolver validates the exact addresses returned to aiohttp's connector;
there is no separate check-then-resolve race. Each new connection is checked,
including redirects. Existing pooled connections stay pinned to their original
public destination. Worker authority and aggregate reservations are owned by
the caller, which must bind this broker behind ToolExecutor.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import math
import socket
import zlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import aiohttp
from yarl import URL


class EgressDenied(ValueError):
    """The request violates the configured public-network boundary."""


def public_address(address: str) -> str:
    """Reject private, translated-private, scoped and special-use addresses."""
    if "%" in address:
        raise EgressDenied("Scoped network addresses are not permitted")
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError as exc:
        raise EgressDenied("Resolver returned an invalid network address") from exc
    if not parsed.is_global or parsed.is_multicast:
        raise EgressDenied("Only public Internet addresses are permitted")
    if isinstance(parsed, ipaddress.IPv4Address) and (
        parsed in ipaddress.ip_network("192.0.0.0/24")
        or parsed == ipaddress.ip_address("168.63.129.16")
    ):
        raise EgressDenied("Cloud platform and special-use addresses are not permitted")
    if isinstance(parsed, ipaddress.IPv6Address):
        # Translation/tunneling could route a public-looking IPv6 destination
        # into a private IPv4 network. No such networks are needed for HTTPS.
        if parsed.ipv4_mapped or parsed.sixtofour or parsed.teredo:
            raise EgressDenied("IPv4 transition addresses are not permitted")
        if parsed in ipaddress.ip_network("64:ff9b::/96") or parsed in ipaddress.ip_network(
            "64:ff9b:1::/48"
        ):
            raise EgressDenied("Translated network addresses are not permitted")
    return str(parsed)


def validate_url(url: str, allowed_domains: Sequence[str] = ()) -> URL:
    """Normalize once and reject ambient credentials and non-HTTP targets."""
    if (
        not isinstance(url, str)
        or len(url) > 8192
        or "\\" in url
        or any(ord(char) < 33 for char in url)
    ):
        raise EgressDenied("Invalid HTTP URL")
    try:
        parsed = URL(url)
        host = parsed.raw_host
        port = parsed.port
    except (ValueError, UnicodeError) as exc:
        raise EgressDenied("Invalid HTTP URL") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not host
        or parsed.user is not None
        or parsed.password is not None
    ):
        raise EgressDenied("Only credential-free HTTP(S) URLs are permitted")
    if port not in {80, 443}:
        raise EgressDenied("Only public HTTP(S) ports 80 and 443 are permitted")
    host = host.rstrip(".").lower()
    if (
        "%" in host
        or host == "localhost"
        or host.endswith((".localhost", ".local", ".internal", ".lan", ".home"))
    ):
        raise EgressDenied("Private hostnames are not permitted")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if "." not in host:
            raise EgressDenied("An absolute public hostname is required") from None
    else:
        public_address(host)
    if allowed_domains:
        permitted = False
        for domain in allowed_domains:
            # Exact hosts plus their subdomains; no URL, port or wildcard syntax.
            if not domain or any(char in domain for char in "/:@*%\\"):
                raise EgressDenied("Invalid allowed domain configuration")
            try:
                normalized = domain.rstrip(".").encode("idna").decode("ascii").lower()
            except UnicodeError as exc:
                raise EgressDenied("Invalid allowed domain configuration") from exc
            if host == normalized or host.endswith("." + normalized):
                permitted = True
        if not permitted:
            raise EgressDenied("Destination is outside the team's network policy")
    return parsed.with_host(host).with_fragment(None)


class PublicResolver(aiohttp.abc.AbstractResolver):
    """Return only validated numeric endpoints to the actual socket connector."""

    async def resolve(
        self, host: str, port: int = 0, family: int = socket.AF_INET
    ) -> list[aiohttp.abc.ResolveResult]:
        rows = await asyncio.get_running_loop().getaddrinfo(
            host, port, type=socket.SOCK_STREAM, family=family
        )
        result: list[aiohttp.abc.ResolveResult] = []
        seen: set[str] = set()
        for address_family, _, protocol, _, sockaddr in rows:
            address = public_address(str(sockaddr[0]))
            if address not in seen:
                result.append(
                    {
                        "hostname": host,
                        "host": address,
                        "port": port,
                        "family": address_family,
                        "proto": protocol,
                        "flags": socket.AI_NUMERICHOST,
                    }
                )
                seen.add(address)
        if not result:
            raise EgressDenied("Destination has no public addresses")
        return result

    async def close(self) -> None:
        """No resolver-owned sockets or background tasks exist."""


@dataclass(frozen=True, slots=True)
class FetchResult:
    url: str
    status: int
    content_type: str
    body: bytes
    sha256: str

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


class EgressClient:
    """One pooled client for the Swarm runtime; close it at runtime shutdown."""

    def __init__(self, *, concurrency: int = 8) -> None:
        if not 1 <= concurrency <= 64:
            raise ValueError("Egress concurrency must be between 1 and 64")
        self._concurrency = concurrency
        self._semaphore = asyncio.Semaphore(concurrency)
        self._session: aiohttp.ClientSession | None = None

    def _client(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(
                    resolver=PublicResolver(),
                    use_dns_cache=False,
                    limit=self._concurrency,
                    limit_per_host=4,
                ),
                trust_env=False,
                cookie_jar=aiohttp.DummyCookieJar(),
                auto_decompress=False,
                headers={
                    "Accept-Encoding": "gzip, deflate",
                    "User-Agent": "PersonalJarvis-Swarm/1",
                },
            )
        return self._session

    async def aclose(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def fetch(
        self,
        url: str,
        *,
        allowed_domains: Sequence[str] = (),
        max_bytes: int = 2_000_000,
        timeout_s: float = 20,
        cancel: Callable[[], bool] | None = None,
    ) -> FetchResult:
        if not 0 < max_bytes <= 50_000_000:
            raise ValueError("HTTP byte limit must be between 1 and 50000000")
        if not math.isfinite(timeout_s) or not 0 < timeout_s <= 120:
            raise ValueError("HTTP timeout must be between 0 and 120 seconds")
        target = validate_url(url, allowed_domains)
        if cancel is not None and cancel():
            raise asyncio.CancelledError("Swarm egress canceled")
        operation = asyncio.create_task(self._fetch(target, allowed_domains, max_bytes, timeout_s))
        try:
            async with asyncio.timeout(timeout_s):
                while not operation.done():
                    if cancel is not None and cancel():
                        raise asyncio.CancelledError("Swarm egress canceled")
                    await asyncio.wait({operation}, timeout=0.05)
                return await operation
        finally:
            if not operation.done():
                operation.cancel()
            # Retrieve every exception and wait until owned sockets are released.
            await asyncio.gather(operation, return_exceptions=True)

    async def _fetch(
        self, url: URL, domains: Sequence[str], max_bytes: int, timeout_s: float
    ) -> FetchResult:
        from jarvis.core.socket_budget import should_defer_optional_io

        if should_defer_optional_io():
            raise EgressDenied("Network capacity is temporarily unavailable")
        async with self._semaphore:
            for _ in range(6):
                target = validate_url(str(url), domains)
                async with self._client().get(
                    target, allow_redirects=False, timeout=aiohttp.ClientTimeout(total=timeout_s)
                ) as response:
                    if response.status in {301, 302, 303, 307, 308}:
                        location = response.headers.get("Location")
                        if not location:
                            raise EgressDenied("Redirect has no destination")
                        url = validate_url(str(target.join(URL(location))), domains)
                        response.close()  # Do not consume an unbounded redirect body.
                        continue
                    encoding = response.headers.get("Content-Encoding", "identity").lower().strip()
                    if encoding not in {"identity", "gzip", "deflate"}:
                        raise EgressDenied("Unsupported HTTP content encoding")
                    inflater = (
                        zlib.decompressobj(
                            16 + zlib.MAX_WBITS if encoding == "gzip" else zlib.MAX_WBITS
                        )
                        if encoding != "identity"
                        else None
                    )
                    if response.content_length is not None and response.content_length > max_bytes:
                        raise EgressDenied("HTTP response exceeds the byte limit")
                    body = bytearray()
                    wire_bytes = 0
                    async for chunk in response.content.iter_chunked(min(max_bytes + 1, 65536)):
                        wire_bytes += len(chunk)
                        if wire_bytes > max_bytes:
                            raise EgressDenied("HTTP response exceeds the wire byte limit")
                        if inflater is not None:
                            try:
                                chunk = inflater.decompress(chunk, max_bytes - len(body) + 1)
                            except zlib.error as exc:
                                raise EgressDenied("Invalid compressed HTTP response") from exc
                            if inflater.unconsumed_tail or inflater.unused_data:
                                raise EgressDenied("Compressed HTTP response exceeds its boundary")
                        if len(body) + len(chunk) > max_bytes:
                            raise EgressDenied("HTTP response exceeds the byte limit")
                        body.extend(chunk)
                    if inflater is not None and not inflater.eof:
                        raise EgressDenied("Truncated compressed HTTP response")
                    data = bytes(body)
                    return FetchResult(
                        str(target),
                        response.status,
                        response.headers.get("Content-Type", "application/octet-stream"),
                        data,
                        hashlib.sha256(data).hexdigest(),
                    )
            raise EgressDenied("HTTP redirect limit exceeded")
