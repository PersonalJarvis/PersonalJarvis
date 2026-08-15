"""Regression: an HTTP 401 from a streamable-http MCP endpoint must surface
as a real exception, never as a bare CancelledError.

Reproduced live 2026-08-15: the mcp SDK's streamable-http transport aborts its
internal anyio task group on a 401, so ``MCPClient.start()`` raised
``CancelledError`` — which skipped every ``except Exception`` upstream and
silently killed the ENTIRE plugin bootstrap task. GitHub is catalog entry #1,
so one expired GitHub credential took every marketplace plugin down on every
boot. The fix unwraps the real transport error in ``_run_lifecycle``.

The fake server speaks just enough HTTP/1.1 to let httpx complete the request
cycle and see the 401 status.
"""
from __future__ import annotations

import asyncio
import re

import pytest

from jarvis.mcp.client import MCPClient, _describe_transport_failure
from jarvis.mcp.registry import MCPServerSpec


async def _handle_with_401(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> None:
    try:
        header_blob = await asyncio.wait_for(
            reader.readuntil(b"\r\n\r\n"), timeout=5
        )
        match = re.search(rb"content-length:\s*(\d+)", header_blob, re.IGNORECASE)
        if match:
            body_len = int(match.group(1))
            if body_len:
                await asyncio.wait_for(reader.readexactly(body_len), timeout=5)
    except (TimeoutError, asyncio.IncompleteReadError, ConnectionError):
        pass
    body = b"Unauthorized"
    writer.write(
        b"HTTP/1.1 401 Unauthorized\r\n"
        b"Content-Type: text/plain\r\n"
        b"Content-Length: " + str(len(body)).encode() + b"\r\n"
        b"Connection: close\r\n\r\n" + body
    )
    try:
        await writer.drain()
        writer.close()
        await writer.wait_closed()
    except ConnectionError:
        pass


@pytest.mark.asyncio
async def test_http_401_surfaces_as_runtime_error_not_cancelled() -> None:
    server = await asyncio.start_server(_handle_with_401, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    spec = MCPServerSpec(
        name="fake401",
        display="Fake 401",
        description="always answers 401",
        install_command=[],
        transport="http",
        url=f"http://127.0.0.1:{port}/mcp/",
    )
    client = MCPClient(spec)
    try:
        with pytest.raises(Exception) as excinfo:
            await client.start()
        # The whole point: the caller must see a REAL exception, not the
        # transport's leaked CancelledError.
        assert not isinstance(excinfo.value, asyncio.CancelledError)
        assert "401" in str(excinfo.value)
    finally:
        await client.stop()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_genuine_cancel_still_propagates() -> None:
    """A real task cancel (shutdown) must NOT be swallowed by the unwrap."""

    async def _handle_hang(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        await asyncio.sleep(30)  # never answers — start() blocks until cancel

    server = await asyncio.start_server(_handle_hang, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    spec = MCPServerSpec(
        name="hang",
        display="Hang",
        description="never answers",
        install_command=[],
        transport="http",
        url=f"http://127.0.0.1:{port}/mcp/",
    )
    client = MCPClient(spec)
    task = asyncio.create_task(client.start())
    await asyncio.sleep(0.3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await client.stop()
    server.close()
    await server.wait_closed()


def test_describe_transport_failure_flattens_groups() -> None:
    inner = RuntimeError("Client error '401 Unauthorized' for url 'x'")
    group = BaseExceptionGroup(
        "unhandled errors",
        [asyncio.CancelledError(), BaseExceptionGroup("nested", [inner])],
    )
    text = _describe_transport_failure(group)
    assert "401" in text
    assert "Cancelled" not in text
