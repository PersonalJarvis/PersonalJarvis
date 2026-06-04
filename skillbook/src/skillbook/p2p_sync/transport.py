"""Transport Protocol (ADR-0006, amended by ADR-0010).

The in-process implementation that previously lived here (``InProcessTransport``)
was moved to ``tests/fakes/transport.py`` per ADR-0010 — production src/ must
not host test doubles. A future real-network transport (TCP, libp2p, WebRTC)
lands here as a sibling to the Protocol.
"""

from __future__ import annotations

from typing import Awaitable, Callable, Protocol, runtime_checkable


@runtime_checkable
class Transport(Protocol):
    async def gossip(self, payload: bytes) -> None: ...
    def subscribe(self, handler: Callable[[bytes], Awaitable[None]]) -> None: ...
