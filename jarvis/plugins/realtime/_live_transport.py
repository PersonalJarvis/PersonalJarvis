"""Local-only Live transport preparation, independent of the host application.

HTTP keeps its existing trust policy. WebSockets use Python's default trust
store (including Windows roots), just as asyncio does for ``ssl=True``. Only
that immutable context is reused; no credentials, sockets or calls are cached.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import os
import threading
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

log = logging.getLogger(__name__)
_tls_lock = threading.Lock()
_tls_entry: tuple[tuple, float, Any] | None = None
_TLS_MAX_AGE_S = 300.0
_cleanup_tasks: set[asyncio.Task] = set()


def startup_websocket_options(mark: Callable[[str], Any]) -> dict[str, Any]:
    """Time the supported connection lifecycle without reading wire content.

    For WSS, connection_made runs after TCP/proxy and TLS setup. Separating it
    from the opening handshake exposes whether the wait is local transport or
    the provider's HTTP upgrade. Optional WebSocket imports remain call-owned.
    """
    from websockets.asyncio.client import ClientConnection

    class StartupConnection(ClientConnection):
        def connection_made(self, transport: Any) -> None:
            super().connection_made(transport)
            mark("control_transport_connected")

        async def handshake(self, *args: Any, **kwargs: Any) -> None:
            mark("control_handshake_started")
            await super().handshake(*args, **kwargs)
            mark("control_handshake_complete")

    return {"create_connection": StartupConnection}


def _trust_key() -> tuple:
    # Replacing an explicitly configured bundle must invalidate the context.
    # OS trust-store edits are picked up within the bounded refresh interval.
    result: list[Any] = []
    for name in ("SSL_CERT_FILE", "SSL_CERT_DIR"):
        path = os.environ.get(name)
        result.append(path)
        try:
            metadata = os.stat(path) if path else None
            result.append((metadata.st_mtime_ns, metadata.st_size) if metadata else None)
        except OSError:
            result.append(None)  # ssl retains its normal missing-path behavior.
    return tuple(result)


def _websocket_context() -> Any:
    """Called on a worker; never parse system certificates on an audio loop."""
    import ssl

    global _tls_entry
    with _tls_lock:
        key = _trust_key()
        entry = _tls_entry
        if entry is not None and entry[0] == key and time.monotonic() - entry[1] < _TLS_MAX_AGE_S:
            return entry[2]
        context = ssl.create_default_context()
        # Publish only after successful verification-store loading. A failed
        # refresh must never silently reuse the preceding trust configuration.
        _tls_entry = (key, time.monotonic(), context)
        return context


async def websocket_options() -> dict[str, Any]:
    return {"ssl": await asyncio.to_thread(_websocket_context)}


async def warm_transport() -> None:
    """Prime imports and certificate parsing without DNS, auth or remote I/O."""
    def prepare() -> None:
        httpx = importlib.import_module("httpx")
        importlib.import_module("websockets.asyncio.client")
        with httpx.Client(timeout=25):
            pass
        _websocket_context()

    await asyncio.to_thread(prepare)


async def _close_client_when_ready(task: asyncio.Task) -> None:
    try:
        client = await task
    except Exception as exc:
        log.debug("Live client preparation failed (%s).", type(exc).__name__)
        return
    try:
        await client.__aexit__(None, None, None)
    except Exception as exc:
        log.warning("Live HTTP client cleanup failed (%s).", type(exc).__name__)


def _cleanup_done(task: asyncio.Task) -> None:
    _cleanup_tasks.discard(task)
    if not task.cancelled():
        task.exception()  # Cleanup reports sanitized errors itself.


@asynccontextmanager
async def preparing_http_client(
    factory: Callable[..., Any] | None = None, **kwargs: Any,
) -> AsyncIterator[asyncio.Task]:
    """Own a client even if hangup wins the race against its worker thread.

    The yielded task may run beside credential loading. Await it through
    shield: cancelling a thread's awaiter cannot stop construction. Teardown
    retains and closes late results without delaying the user's hangup.
    """
    def construct() -> Any:
        create = factory or importlib.import_module("httpx").AsyncClient
        return create(**kwargs)

    async def enter() -> Any:
        client = await asyncio.to_thread(construct)
        try:
            await client.__aenter__()
        except BaseException:
            await client.__aexit__(None, None, None)
            raise
        return client

    task = asyncio.create_task(enter(), name="live-http-prepare")
    try:
        yield task
    finally:
        cleanup = asyncio.create_task(_close_client_when_ready(task), name="live-http-cleanup")
        _cleanup_tasks.add(cleanup)
        cleanup.add_done_callback(_cleanup_done)
        if task.done():
            await asyncio.shield(cleanup)

