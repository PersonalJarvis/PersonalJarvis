"""One HTTP client per caller, kept warm — instead of one per request.

Opening a fresh ``httpx`` client per call costs a new TCP handshake (and a new
TLS handshake, and a fresh ``ssl.create_default_context`` that parses the whole
CA bundle) every single time. On a hot path that is measurable latency; on a
POLLING path it is something worse.

Every connection borrows an ephemeral port from a pool the operating system
owns, and a closed connection keeps that port through TIME_WAIT — 120 seconds
on Windows. A loop that opens one connection per tick and never reuses it turns
a harmless 5-second poll into a permanent standing population of dead sockets,
and several such loops together can empty the pool. When that happens NOTHING
on the machine can connect: not this app, not the file manager, not the
browser. That is BUG-215, and one of the loops behind it was probing
``127.0.0.1/api/health`` every 250 ms with a brand-new client each time.

So: build the client once, let it keep its connections alive, and let the
caller stop thinking about sockets. See :mod:`jarvis.core.socket_budget` for
the watchdog that notices when something still gets this wrong.

Both pools rebind whenever the running event loop changes (async) or the client
is closed, so a cached client is never reused across loops — each
``pytest-asyncio`` test runs in its own loop, and a pool reused across loops
would otherwise raise ``RuntimeError: Event loop is closed``.

Usage::

    from jarvis.core.http_pool import HttpClientPool, SyncHttpClientPool

    self._pool = HttpClientPool(timeout_s=10.0)
    resp = await self._pool.client().get(url)

    self._probe = SyncHttpClientPool(timeout_s=1.0)
    resp = self._probe.client().get("http://127.0.0.1:47821/api/health")
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:  # pragma: no cover - typing only
    import httpx

#: Keep-alive ceiling for a pooled client. Ten idle connections per client is
#: httpx's own default and plenty for the handful of hosts any one caller talks
#: to; the value is pinned here so the intent survives an upstream change.
#:
#: ``keepalive_expiry`` is the number that matters for the port pool: a
#: connection parked longer than this is closed and its port starts its
#: TIME_WAIT. Thirty seconds comfortably outlives the polling intervals in this
#: app (1-15 s), so a poller reuses one connection forever instead of leaving a
#: corpse behind every tick.
MAX_KEEPALIVE_CONNECTIONS: Final[int] = 10
MAX_CONNECTIONS: Final[int] = 50
KEEPALIVE_EXPIRY_S: Final[float] = 30.0


def default_limits() -> httpx.Limits:
    """The connection limits every pooled client in this app is built with."""
    import httpx

    return httpx.Limits(
        max_keepalive_connections=MAX_KEEPALIVE_CONNECTIONS,
        max_connections=MAX_CONNECTIONS,
        keepalive_expiry=KEEPALIVE_EXPIRY_S,
    )


class HttpClientPool:
    """Lazily create and reuse one ``httpx.AsyncClient`` (keep-alive pool).

    Args:
        timeout_s: Request timeout. Defaults to the shared REST budget.
        transport: Forwarded verbatim so tests can inject an
            ``httpx.MockTransport`` exactly as they did with per-request clients.
        client_kwargs: Extra keyword arguments for the client — e.g. the
            ``event_hooks`` from :func:`jarvis.core.http_guard.https_only_async`.
        factory: Builds the client instead of this class, when a module already
            has its own construction point that tests replace. Resolved on
            every build, so a monkeypatched factory is honoured — pair it with
            :meth:`forget` in the test reset so each test builds afresh.
    """

    def __init__(
        self,
        *,
        timeout_s: float | None = None,
        transport: Any | None = None,
        client_kwargs: dict[str, Any] | None = None,
        factory: Callable[[], Any] | None = None,
    ) -> None:
        if timeout_s is None:
            # The shared voice tool budget (5 s): a round trip not back by then
            # cannot make the spoken turn any more — fail it, free the
            # connection, log the cause. A tool that knows better passes its own.
            from jarvis.core.tool_budget import REST_REQUEST_TIMEOUT_S

            timeout_s = REST_REQUEST_TIMEOUT_S
        self._timeout_s = timeout_s
        self._transport = transport
        self._client_kwargs = dict(client_kwargs or {})
        self._factory = factory
        self._client: Any | None = None
        self._loop: Any | None = None

    def client(self) -> Any:
        """Return the pooled client, (re)binding it to the current loop."""
        import asyncio

        loop = asyncio.get_running_loop()
        client = self._client
        if client is None or self._loop is not loop or client.is_closed:
            client = self._build()
            self._client = client
            self._loop = loop
        return client

    def _build(self) -> Any:
        if self._factory is not None:
            return self._factory()
        import httpx

        return httpx.AsyncClient(
            timeout=self._timeout_s,
            transport=self._transport,
            limits=default_limits(),
            **self._client_kwargs,
        )

    def forget(self) -> None:
        """Drop the cached client WITHOUT closing it. Tests only.

        A test reset runs synchronously and cannot await ``aclose``; its
        clients ride a ``MockTransport`` and own no socket, so dropping the
        reference costs nothing. Production shutdown uses :meth:`aclose`.
        """
        self._client = None
        self._loop = None

    async def aclose(self) -> None:
        """Close the pooled client (best-effort; safe to call repeatedly)."""
        client = self._client
        self._client = None
        self._loop = None
        if client is not None and not client.is_closed:
            await client.aclose()


class SyncHttpClientPool:
    """The same idea for ``httpx.Client``, for probes that are not on a loop.

    The readiness probes this replaces run on plain threads — the desktop
    shell's backend wait, the local-runtime monitor — so they cannot use the
    async pool. Thread-safe: several watchdog threads may share one probe.

    Args:
        timeout_s: Request timeout. Required; a probe's budget is never the
            REST default.
        transport: Injected for tests, as above.
    """

    def __init__(self, *, timeout_s: float, transport: Any | None = None) -> None:
        self._timeout_s = float(timeout_s)
        self._transport = transport
        self._client: Any | None = None
        self._lock = threading.Lock()

    def client(self) -> Any:
        """Return the pooled client, rebuilding it if it was closed."""
        import httpx

        with self._lock:
            client = self._client
            if client is None or client.is_closed:
                client = httpx.Client(
                    timeout=self._timeout_s,
                    transport=self._transport,
                    limits=default_limits(),
                )
                self._client = client
            return client

    def close(self) -> None:
        """Close the pooled client (best-effort; safe to call repeatedly)."""
        with self._lock:
            client = self._client
            self._client = None
        if client is not None and not client.is_closed:
            try:
                client.close()
            except Exception as exc:  # noqa: BLE001
                from loguru import logger

                logger.debug("HTTP pool: closing a probe client failed ({}).", exc)


__all__ = [
    "KEEPALIVE_EXPIRY_S",
    "MAX_CONNECTIONS",
    "MAX_KEEPALIVE_CONNECTIONS",
    "HttpClientPool",
    "SyncHttpClientPool",
    "default_limits",
]
