"""Authenticated Chrome-extension transports; no browser or credential-store access.

The HTTP route authenticates the first private WebSocket message before attaching.
Commands belong to exactly one connection. They are never replayed after reconnect.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

log = logging.getLogger(__name__)
MAX_MESSAGE = 8 * 1024 * 1024
OPERATIONS = frozenset({"ensure", "observe", "action", "takeover", "snapshot", "shutdown"})


@dataclass
class _Connection:
    socket: Any
    pending: dict[str, asyncio.Future] = field(default_factory=dict)
    write_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class ChromeConnections:
    """One authenticated installation per profile, with terminal disconnects."""

    def __init__(self) -> None:
        self._connections: dict[str, _Connection] = {}
        self._listeners: dict[str, set[Callable[[dict], None]]] = {}
        self._attach_locks: dict[str, asyncio.Lock] = {}

    def connected(self, profile_id: str) -> bool:
        return profile_id in self._connections

    def subscribe(self, profile_id: str, callback: Callable[[dict], None]) -> Callable[[], None]:
        listeners = self._listeners.setdefault(profile_id, set())
        listeners.add(callback)
        return lambda: listeners.discard(callback)

    def _publish(self, profile_id: str, event: dict) -> None:
        for callback in tuple(self._listeners.get(profile_id, ())):
            try:
                callback(event)
            except Exception:
                log.warning("Chrome connection observer failed", exc_info=True)

    async def change_credentials(self, profile_id: str, change: Any) -> Any:
        """Rotate/revoke credentials atomically with authenticated transport ownership."""
        async with self._attach_locks.setdefault(profile_id, asyncio.Lock()):
            result = await change()
            await self.detach(profile_id)
            return result

    async def attach(self, profile_id: str, websocket: Any, *, authorize: Any = None) -> None:
        async with self._attach_locks.setdefault(profile_id, asyncio.Lock()):
            if authorize is not None and not await authorize():
                await websocket.close(code=4401)
                return
            await self.detach(profile_id)
            connection = _Connection(websocket)
            self._connections[profile_id] = connection
        try:
            if authorize is not None:
                await websocket.send_json({"kind": "connected"})
            while True:
                message = await websocket.receive_json()
                if not isinstance(message, dict):
                    raise ValueError("Invalid Chrome message")
                kind = message.get("kind")
                if kind == "ping":
                    async with connection.write_lock:
                        await websocket.send_json({"kind": "pong"})
                elif kind == "response":
                    future = connection.pending.get(message.get("id"))
                    if future is not None and not future.done():
                        if message.get("ok") is True and isinstance(message.get("result"), dict):
                            future.set_result(message["result"])
                        else:
                            # Extension error text cannot expose page content or credentials.
                            future.set_exception(
                                RuntimeError("Chrome operation failed; check Chrome")
                            )
                elif kind in {"state", "frame", "pointer", "warning", "disconnected"}:
                    self._publish(profile_id, message)
                else:
                    raise ValueError("Unsupported Chrome message")
        except asyncio.CancelledError:
            raise
        except Exception:
            # Every transport/read failure is terminal. The extension reconnects with jitter.
            log.debug("Chrome transport disconnected for profile %s", profile_id)
        finally:
            if self._connections.get(profile_id) is connection:
                self._connections.pop(profile_id, None)
                self._publish(profile_id, {"kind": "disconnected"})
            self._fail_pending(connection)
            try:
                await websocket.close(code=1000)
            except Exception:
                log.debug("Chrome socket was already closed")

    @staticmethod
    def _fail_pending(connection: _Connection) -> None:
        for future in connection.pending.values():
            if not future.done():
                future.set_exception(RuntimeError("Chrome disconnected; action was not replayed"))

    async def request(
        self,
        profile_id: str,
        op: str,
        args: dict,
        timeout: float = 60,  # noqa: ASYNC109 -- bounded protocol deadline
    ) -> dict:
        if op not in OPERATIONS:
            raise ValueError("Unsupported Chrome operation")
        connection = self._connections.get(profile_id)
        if connection is None:
            raise RuntimeError("Connect this Chrome profile in its Jarvis extension first")
        key = uuid4().hex
        future = asyncio.get_running_loop().create_future()
        connection.pending[key] = future
        try:
            async with connection.write_lock:
                await connection.socket.send_json(
                    {"kind": "command", "id": key, "op": op, "args": args}
                )
            return await asyncio.wait_for(future, timeout)
        except TimeoutError:
            if op == "action":
                # Chrome may already have acted; revoke the transport so no
                # later input step can continue or get replayed by a planner.
                await self.detach(profile_id)
            raise
        finally:
            connection.pending.pop(key, None)

    async def detach(self, profile_id: str) -> None:
        connection = self._connections.pop(profile_id, None)
        if connection is not None:
            self._fail_pending(connection)
            closing = asyncio.create_task(connection.socket.close(code=1000))
            try:
                await asyncio.shield(closing)
            except asyncio.CancelledError:
                # A session observer may cancel its own request on disconnect.
                # Transport close has separate ownership and must finish first.
                await closing
                raise
            except Exception:
                log.debug("Chrome socket was already closed")
            finally:
                self._publish(profile_id, {"kind": "disconnected"})

    async def close(self) -> None:
        for profile_id in tuple(self._connections):
            await self.detach(profile_id)
