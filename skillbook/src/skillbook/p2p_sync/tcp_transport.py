"""AsyncioTcpTransport: a real TCP-based Transport implementation (stdlib only).

Closes the gap identified in skillbook/FORENSICS.md Q2: the previous
``transport.py`` module contained only a Protocol declaration with no
concrete production implementation, forcing tests/fakes to be imported
from production code paths.

Framing: each gossip payload is prefixed with a 4-byte big-endian unsigned
length, then the raw bytes. The receiving side reads exactly that many bytes
and dispatches to subscribed handlers. Subscriber exceptions are isolated
per the same AP-18 convention used elsewhere.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Awaitable, Callable

_LENGTH_BYTES = 4
_MAX_PAYLOAD = 16 * 1024 * 1024  # 16 MiB hard cap to bound an attacker's pre-allocation


class AsyncioTcpTransport:
    """Concrete Transport using ``asyncio.start_server`` + ``open_connection``."""

    def __init__(
        self,
        *,
        listen_host: str,
        listen_port: int,
        peer_addrs: Sequence[tuple[str, int]] = (),
    ) -> None:
        self._listen_host = listen_host
        self._listen_port = listen_port
        self._peer_addrs: list[tuple[str, int]] = list(peer_addrs)
        self._handlers: list[Callable[[bytes], Awaitable[None]]] = []
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        if self._server is not None:
            return
        self._server = await asyncio.start_server(
            self._on_connection, self._listen_host, self._listen_port
        )

    async def stop(self) -> None:
        if self._server is None:
            return
        self._server.close()
        try:
            await self._server.wait_closed()
        finally:
            self._server = None

    def subscribe(self, handler: Callable[[bytes], Awaitable[None]]) -> None:
        self._handlers.append(handler)

    def add_peer(self, host: str, port: int) -> None:
        self._peer_addrs.append((host, port))

    async def gossip(self, payload: bytes) -> None:
        if len(payload) > _MAX_PAYLOAD:
            raise ValueError(
                f"gossip payload exceeds {_MAX_PAYLOAD} byte cap: {len(payload)}"
            )
        header = len(payload).to_bytes(_LENGTH_BYTES, "big")
        framed = header + payload

        for host, port in list(self._peer_addrs):
            try:
                reader, writer = await asyncio.open_connection(host, port)
            except (ConnectionRefusedError, OSError):
                continue
            try:
                writer.write(framed)
                await writer.drain()
            except (ConnectionResetError, BrokenPipeError, OSError):
                pass
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except (ConnectionResetError, OSError):
                    pass

    async def _on_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            length_bytes = await reader.readexactly(_LENGTH_BYTES)
            length = int.from_bytes(length_bytes, "big")
            if length <= 0 or length > _MAX_PAYLOAD:
                return
            payload = await reader.readexactly(length)
        except asyncio.IncompleteReadError:
            return
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionResetError, OSError):
                pass

        for handler in list(self._handlers):
            try:
                await handler(payload)
            except Exception:
                continue
