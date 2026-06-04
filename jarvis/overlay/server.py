"""WS-IPC-Server fuer den Hauptjarvis-Prozess.

Plan §10.5 + §20.3: WS-Server bindet auf 127.0.0.1 + freien Port aus
``[overlay].ws_port..ws_port_range_max``. Bridge ist die Faceade,
``websockets.serve()`` ist der Wire-Layer.
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
    """Was ``start_ipc_server`` zurueckgibt."""

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
    """Suche freien Port in ``[port, port_range_max]`` und starte WS-Server.

    Plan-Constraint: ``host`` muss Loopback sein (Pydantic-Validator
    erzwingt das beim Config-Load; hier defensiv nochmal pruefen).

    ``path`` wird bewusst nicht enforced — der Plan §10 nennt
    ``/overlay`` als Convention; der ``websockets``-Lib-Handler sieht
    aber alle URIs unter dem Bind-Host. Pfad-Filter machen wir im
    Bridge-Handler nur wenn noetig (aktuell nicht).
    """
    if host not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError(f"WS-Server muss auf Loopback binden, host={host!r}")
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

    # Alle Ports belegt.
    await bridge.stop()
    raise RuntimeError(
        f"kein freier Port in [{port}, {port_range_max}] (last={last_err!r})"
    )


__all__ = ["IPCServerHandle", "start_ipc_server"]
