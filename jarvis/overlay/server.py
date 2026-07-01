"""WS IPC server for the main-Jarvis process.

Plan §10.5 + §20.3: the WS server binds to 127.0.0.1 + a free port from
``[overlay].ws_port..ws_port_range_max``. The bridge is the facade,
``websockets.serve()`` is the wire layer.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

import websockets
from websockets.asyncio.server import Server, serve

from jarvis.overlay.bridge import OverlayBridge

logger = logging.getLogger(__name__)


@dataclass
class IPCServerHandle:
    """What ``start_ipc_server`` returns."""

    bridge: OverlayBridge
    server: Server
    host: str
    port: int

    async def stop(self) -> None:
        self.server.close()
        await self.server.wait_closed()
        await self.bridge.stop()


async def start_ipc_server(
    *,
    host: str = "127.0.0.1",
    port: int = 7842,
    port_range_max: int = 7852,
    bridge: Optional[OverlayBridge] = None,
    path: str = "/overlay",
) -> IPCServerHandle:
    """Search for a free port in ``[port, port_range_max]`` and start the WS server.

    Plan constraint: ``host`` must be loopback (a Pydantic validator
    enforces this at config load; checked defensively again here).

    ``path`` is deliberately not enforced — Plan §10 names
    ``/overlay`` as the convention; the ``websockets`` library handler,
    however, sees all URIs under the bind host. We only do path
    filtering in the bridge handler if needed (currently not).
    """
    if host not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError(f"WS server must bind to loopback, host={host!r}")
    if port_range_max < port:
        raise ValueError(f"port_range_max < port ({port_range_max} < {port})")

    bridge = bridge or OverlayBridge()
    await bridge.start()

    last_err: Optional[BaseException] = None
    for candidate in range(port, port_range_max + 1):
        try:
            server = await serve(bridge.handler, host, candidate)
        except OSError as exc:
            last_err = exc
            continue
        logger.info("overlay WS-Server listening on ws://%s:%d%s", host, candidate, path)
        return IPCServerHandle(bridge=bridge, server=server, host=host, port=candidate)

    # All ports taken.
    await bridge.stop()
    raise RuntimeError(
        f"no free port in [{port}, {port_range_max}] (last={last_err!r})"
    )


__all__ = ["IPCServerHandle", "start_ipc_server"]
