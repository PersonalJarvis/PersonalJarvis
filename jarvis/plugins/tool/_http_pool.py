"""Shared connection-pool helper for the REST-backed router tools.

Opening a fresh ``httpx.AsyncClient`` per request forces a new TCP + TLS
handshake to the same host on every call. The REST plugins (gmail, vercel)
issue several such calls per user turn and across a session, so that repeated
handshake is a real, avoidable slice of the "plugin call takes ages" latency.
This helper hands back ONE client per tool instance and keeps its connections
warm (HTTP keep-alive) across calls.

The client is rebound whenever the running event loop changes, so a cached
client is never reused across loops — each ``pytest-asyncio`` test runs in its
own loop, and a tool instance reused across loops would otherwise raise
``RuntimeError: Event loop is closed``.
"""
from __future__ import annotations

from typing import Any


class HttpClientPool:
    """Lazily create and reuse one ``httpx.AsyncClient`` (keep-alive pool).

    ``transport`` is forwarded verbatim so tests can inject an
    ``httpx.MockTransport`` exactly as they did with the per-request clients.
    """

    def __init__(
        self, *, timeout_s: float = 20.0, transport: Any | None = None
    ) -> None:
        self._timeout_s = timeout_s
        self._transport = transport
        self._client: Any | None = None
        self._loop: Any | None = None

    def client(self) -> Any:
        """Return the pooled client, (re)binding it to the current loop."""
        import asyncio

        import httpx

        loop = asyncio.get_running_loop()
        client = self._client
        if client is None or self._loop is not loop or client.is_closed:
            client = httpx.AsyncClient(
                timeout=self._timeout_s, transport=self._transport
            )
            self._client = client
            self._loop = loop
        return client

    async def aclose(self) -> None:
        """Close the pooled client (best-effort; safe to call repeatedly)."""
        client = self._client
        self._client = None
        self._loop = None
        if client is not None and not client.is_closed:
            await client.aclose()
