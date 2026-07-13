"""Stdio MCP adapter for a mission-scoped supervisor tool grant."""

from __future__ import annotations

import asyncio
import http.client
import io
import json
import os
import sys
from typing import Any
from urllib.parse import urlsplit

import anyio
from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from .worker_tool_broker import (
    BROKER_EXECUTION_TIMEOUT_S,
    BROKER_TOKEN_ENV,
    BROKER_URL_ENV,
)

# The client must outlive the supervisor-side wait so it can receive the
# broker's deterministic timeout response instead of dropping the socket first.
_HTTP_TIMEOUT_S = BROKER_EXECUTION_TIMEOUT_S + 5.0


def _endpoint() -> tuple[str, int]:
    parsed = urlsplit(os.environ.get(BROKER_URL_ENV, ""))
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "::1", "localhost"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is None
    ):
        raise RuntimeError("Worker tool broker URL must be an authenticated loopback endpoint.")
    return parsed.hostname, parsed.port


def _request(method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    host, port = _endpoint()
    token = os.environ.get(BROKER_TOKEN_ENV, "")
    if not token:
        raise RuntimeError("Worker tool broker grant is missing or expired.")
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Authorization": f"Bearer {token}"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    conn = http.client.HTTPConnection(host, port, timeout=_HTTP_TIMEOUT_S)
    try:
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        raw = response.read()
    finally:
        conn.close()
    data = json.loads(raw or b"{}")
    if response.status >= 400:
        raise RuntimeError(str(data.get("error") or f"broker HTTP {response.status}"))
    return data


server = Server("jarvis-worker-tools")


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    payload = await asyncio.to_thread(_request, "GET", "/v1/tools")
    tools: list[types.Tool] = []
    for item in payload.get("tools") or []:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        schema = item.get("input_schema")
        if not isinstance(schema, dict):
            schema = {"type": "object", "properties": {}}
        tools.append(
            types.Tool(
                name=str(item["name"]),
                description=str(item.get("description") or ""),
                inputSchema=schema,
            )
        )
    return tools


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
    try:
        payload = await asyncio.to_thread(
            _request,
            "POST",
            "/v1/execute",
            {"name": name, "arguments": arguments},
        )
    except Exception as exc:  # noqa: BLE001 - MCP error response, never a crash
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=str(exc))],
            isError=True,
        )
    text = json.dumps(payload, ensure_ascii=False, default=str)
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=text)],
        structuredContent=payload,
        isError=not bool(payload.get("success")),
    )


async def _main() -> None:
    stdin = None
    stdout = None
    # PyInstaller's windowed bootloader sets Python's standard streams to None.
    # A worker CLI still launches this internal mode with inherited pipe handles,
    # so re-wrap file descriptors 0/1 instead of requiring a separate Python
    # installation beside the desktop bundle.
    if sys.stdin is None:
        raw_stdin = os.fdopen(0, "rb", closefd=False)
        stdin = anyio.wrap_file(
            io.TextIOWrapper(raw_stdin, encoding="utf-8", errors="replace")
        )
    if sys.stdout is None:
        raw_stdout = os.fdopen(1, "wb", closefd=False)
        stdout = anyio.wrap_file(io.TextIOWrapper(raw_stdout, encoding="utf-8"))

    async with stdio_server(stdin=stdin, stdout=stdout) as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def main() -> int:
    """Run the private stdio adapter from Python or the frozen Jarvis binary."""
    asyncio.run(_main())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
