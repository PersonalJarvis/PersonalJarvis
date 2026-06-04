"""Hand-built ``AdminTransport`` fake (Wave 3, sub-task 3.1; EK-3).

Per CLAUDE.md the project uses real fakes, never ``unittest.mock``. A real named
pipe (Windows) or AF_UNIX socket (POSIX) cannot be exercised in a pure-logic unit
test, so :class:`FakeAdminTransport` round-trips raw envelope bytes entirely
in-process: the server-side ``handler`` (the reused
``ipc.AdminPipeServer.handle_raw`` chain) is held and invoked directly by
``roundtrip``. This lets a test prove that a signed envelope flows through
``_decode_request`` -> executor -> ``_encode_response`` with **no** real
transport, exactly the seam the AD-12 security core operates on.

It is structurally compatible with
:class:`jarvis.admin.transport.AdminTransport`: ``serve`` / ``roundtrip`` /
``stop``.
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

RequestHandler = Callable[[bytes], Awaitable[bytes]]


class FakeAdminTransport:
    """In-process ``AdminTransport``: ``roundtrip`` calls the served handler.

    Usage in a loopback-style test::

        transport = FakeAdminTransport()
        server = AdminPipeServer(secret, name, executor, transport=transport)
        serve = asyncio.create_task(server.serve_forever())
        client = AdminPipeClient(secret, name, transport=transport)
        resp = await client.send(op)        # routed through handle_raw, no pipe
        server.stop()

    Or directly, without an ``AdminPipeServer``::

        transport = FakeAdminTransport()
        await transport.serve(my_handler)   # registers the handler, returns
        out = await transport.roundtrip(raw)
    """

    def __init__(self) -> None:
        self._handler: RequestHandler | None = None
        self._handler_ready = asyncio.Event()
        self._stopped = asyncio.Event()
        self.served = False
        self.roundtrips: list[bytes] = []
        self.stop_calls = 0

    async def serve(self, handler: RequestHandler) -> None:
        """Register ``handler`` and block until :meth:`stop` (like a real serve)."""
        self._handler = handler
        self.served = True
        self._handler_ready.set()
        await self._stopped.wait()

    async def roundtrip(self, raw: bytes) -> bytes:
        """Invoke the served handler directly, returning its raw response.

        If no server was started, raises ``FileNotFoundError`` to mirror the
        named-pipe client's "helper not available" path (``AdminPipeClient``
        maps it to ``helper_unavailable``).
        """
        self.roundtrips.append(raw)
        # Allow ``serve`` (scheduled as a task) a chance to register first.
        if self._handler is None:
            try:
                await asyncio.wait_for(self._handler_ready.wait(), timeout=1.0)
            except (TimeoutError, asyncio.TimeoutError):
                pass
        if self._handler is None:
            raise FileNotFoundError("fake admin transport: no server bound")
        return await self._handler(raw)

    def stop(self) -> None:
        self.stop_calls += 1
        self._stopped.set()


__all__ = ["FakeAdminTransport", "RequestHandler"]
