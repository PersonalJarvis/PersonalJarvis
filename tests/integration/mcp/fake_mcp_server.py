"""Minimal MCP server for tests — speaks MCP via stdio.

Uses the official `mcp` Python lib (FastMCP) so the protocol is
implemented correctly — otherwise we'd have to build too much
handshake logic ourselves (initialize, notifications/initialized,
capabilities).

Modes (via ENV `FAKE_MCP_MODE`):
- "ok"        → the echo tool returns `{"echoed": args["msg"]}`
- "fail"      → the echo tool raises an exception

The script is started as a subprocess (`python fake_mcp_server.py`) by
an `MCPClient`.
"""
from __future__ import annotations

import os
import sys

from mcp.server.fastmcp import FastMCP

mode = os.environ.get("FAKE_MCP_MODE", "ok")
mcp = FastMCP("fake-mcp")


@mcp.tool()
def echo(msg: str) -> str:
    """Echoes the input message."""
    if mode == "fail":
        raise RuntimeError("simulated failure")
    return f"echoed:{msg}"


if __name__ == "__main__":
    # FastMCP.run() with default "stdio" — blocks until the transport closes
    try:
        mcp.run("stdio")
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"fake-mcp crashed: {e}\n")
        sys.exit(1)
