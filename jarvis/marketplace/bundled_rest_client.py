"""In-process MCP client interface for the bundled, allowlisted REST tools.

The same tools have a stdio entry point for delegated workers. In the live app,
read the token store for every operation so background OAuth rotation takes
effect immediately, and disconnecting revokes even a retained tool reference.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from jarvis.marketplace.token_store import Tokens
from jarvis.plugins.tool.connected_server import ConnectedRestClient


class BundledRestMcpClient:
    def __init__(self, spec: Any, *, transport: Any = None) -> None:
        self.spec = spec
        self._transport = transport
        self._token_provider: Callable[[], Tokens | None] = lambda: None
        self._rest: ConnectedRestClient | None = None
        self._access: str | None = None
        self._lock = asyncio.Lock()

    def set_token_provider(self, provider: Callable[[], Tokens | None]) -> None:
        self._token_provider = provider

    async def start(self) -> None:
        async with self._lock:
            await self._current_client()

    async def _current_client(self) -> ConnectedRestClient:
        tokens = self._token_provider()
        if tokens is None or tokens.needs_reauth:
            raise RuntimeError("Plugin disconnected or authorization expired; reconnect in Plugins")
        if self._rest is None or tokens.access != self._access:
            if self._rest is not None:
                await self._rest.close()
            self._rest = ConnectedRestClient(
                self.spec.name,
                tokens.access,
                transport=self._transport,
                auth_type="oauth" if tokens.extra.get("client_id") else "pat",
            )
            self._access = tokens.access
        return self._rest

    async def list_tools(self) -> list[dict[str, Any]]:
        async with self._lock:
            return (await self._current_client()).list_tools()

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        async with self._lock:
            return await (await self._current_client()).call(name, arguments)

    async def stop(self) -> None:
        async with self._lock:
            if self._rest is not None:
                await self._rest.close()
            self._rest = None
            self._access = None
