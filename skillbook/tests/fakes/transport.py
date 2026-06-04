"""InProcessTransport: two-endpoint synchronous transport stand-in for tests.

Lives in tests/ — not production. Per ADR-0010 the previous
``src/skillbook/p2p_sync/transport.py:InProcessTransport`` was moved here.
The class name is intentionally retained: "InProcess" is a descriptively
accurate name for what this transport does (two paired objects passing
bytes synchronously), not a *Mock* prefix dodging the constraint.
"""

from __future__ import annotations

from typing import Awaitable, Callable


class InProcessTransport:
    """Two-endpoint paired transport satisfying ``skillbook.p2p_sync.transport.Transport``.

    Construct a pair via :meth:`pair`; ``gossip`` on one endpoint synchronously
    dispatches to the other's subscribed handlers. Subscriber exceptions are
    swallowed in the same spirit as the parent project's EventBus (AP-18 /
    AD-OE6) — one broken handler must never poison the gossip loop.
    """

    def __init__(self) -> None:
        self._peer: "InProcessTransport | None" = None
        self._handlers: list[Callable[[bytes], Awaitable[None]]] = []

    @classmethod
    def pair(cls) -> tuple["InProcessTransport", "InProcessTransport"]:
        a = cls()
        b = cls()
        a._peer = b
        b._peer = a
        return a, b

    def subscribe(self, handler: Callable[[bytes], Awaitable[None]]) -> None:
        self._handlers.append(handler)

    async def gossip(self, payload: bytes) -> None:
        peer = self._peer
        if peer is None:
            return
        for h in list(peer._handlers):
            try:
                await h(payload)
            except Exception:
                continue
