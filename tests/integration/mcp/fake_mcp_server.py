"""Minimaler MCP-Server für Tests — spricht MCP via stdio.

Nutzt die offizielle `mcp`-Python-Lib (FastMCP), damit das Protokoll
korrekt implementiert ist — wir würden sonst zu viel Handshake-Logik
(initialize, notifications/initialized, capabilities) selbst bauen.

Modi (via ENV `FAKE_MCP_MODE`):
- "ok"        → echo-Tool liefert `{"echoed": args["msg"]}`
- "fail"      → echo-Tool wirft Exception

Das Script wird als Subprocess (`python fake_mcp_server.py`) durch
einen `MCPClient` gestartet.
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
    # FastMCP.run() mit default "stdio" — blockiert bis Transport schließt
    try:
        mcp.run("stdio")
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"fake-mcp crashed: {e}\n")
        sys.exit(1)
