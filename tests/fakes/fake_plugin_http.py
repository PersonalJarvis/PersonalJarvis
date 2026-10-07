"""An OAuth provider and MCP transport with observable credential rotation."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx

from jarvis.marketplace.token_store import Tokens


class RotatingPluginHandler:
    def __init__(self) -> None:
        self.calls: list[str | None] = []
        self.entered = asyncio.Event()
        self.release: asyncio.Event | None = None
        self.error: Exception | None = None

    async def refresh(self, current: Tokens) -> Tokens:
        self.calls.append(current.refresh)
        self.entered.set()
        if self.release is not None:
            await self.release.wait()
        if self.error is not None:
            raise self.error
        return Tokens(
            access="fresh-access",
            refresh="fresh-refresh",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )


class PluginMcpTransport:
    """Exercise the real MCP SDK over HTTPX without sockets or provider calls."""

    def __init__(self) -> None:
        self.accepted = "old-access"
        self.requests: list[tuple[str, str]] = []
        self.tool_calls = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        header = request.headers.get("Authorization", "")
        if request.method != "POST":
            return httpx.Response(405)
        body = json.loads(request.content)
        method = body.get("method", "")
        self.requests.append((method, header))
        if header != f"Bearer {self.accepted}":
            return httpx.Response(401)
        if method.startswith("notifications/"):
            return httpx.Response(202)
        if method == "initialize":
            result = {
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "test-plugin", "version": "1"},
            }
        elif method == "tools/list":
            result = {"tools": [{"name": "list_items", "inputSchema": {"type": "object"}}]}
        elif method == "tools/call":
            self.tool_calls += 1
            result = {"content": [{"type": "text", "text": "items"}], "isError": False}
        else:
            raise AssertionError(f"Unexpected MCP method: {method}")
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})
