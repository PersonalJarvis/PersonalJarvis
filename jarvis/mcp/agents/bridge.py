"""stdio ↔ HTTP bridge, so any MCP client can reach the agent ecosystem.

The canonical Agent MCP surface is Streamable HTTP on the running app
(``/api/control/mcp/agents``). That is the right transport — but most desktop
clients (Claude Desktop, Cursor, VS Code, Codex) launch a *command* and speak
stdio to it, and the ones that do accept a URL make a bearer header on a
loopback address awkward to configure. This module is the adapter: a tiny stdio
server that forwards every request to the HTTP surface and hands the answer
back.

    python -m jarvis.mcp.agents.bridge

Configuration is environment first, so the bridge also works from another
machine or a container:

* ``JARVIS_API_URL``     — base URL of the Jarvis to drive. Default: the local
  instance's admin API from this box's config.
* ``JARVIS_MCP_TOKEN``   — a per-client token (what a paired config carries).
  Preferred: it is scoped and can be revoked without touching other clients.
* ``JARVIS_CONTROL_KEY`` — the master control key. Default: read the same way
  the app does (keyring → env → file), which needs no configuration locally.

**A dead Jarvis is not a dead bridge.** When the app is not running, the client
still connects and gets a tool list with one honest sentence explaining what to
start, instead of a red "server failed" that says nothing. That matters because
a desktop client typically launches the bridge at ITS startup, long before the
person opens Jarvis.
"""

from __future__ import annotations

import contextlib
import logging
import os
import sys
from collections.abc import AsyncIterator
from typing import Any, Final

log = logging.getLogger(__name__)

#: Path of the agent surface under the Control API.
SURFACE_PATH: Final[str] = "/api/control/mcp/agents"

#: The shipped admin API port, used only when nothing else answers.
_DEFAULT_PORT: Final[int] = 47821

#: How long a forwarded call may take. Generous, because ``agent_chat`` waits
#: for a model turn on purpose; the tool caps its own wait at 600 s.
CALL_TIMEOUT_S: Final[float] = 660.0


def api_base_url() -> str:
    """Where Jarvis actually listens.

    ``JARVIS_API_URL`` wins, for a bridge pointed at another machine. Otherwise
    ask ``cli_ctl.discovery``, which reads the running instance's own session
    file: that is authoritative even when the app bound a non-default port or
    is running as a second instance, and it verifies the process is still
    alive rather than pointing at a recycled port. Only if nothing is running
    do we fall back to the configured port, and then to the shipped default.
    """
    from_env = os.environ.get("JARVIS_API_URL", "").strip().rstrip("/")
    if from_env:
        return from_env
    try:
        from jarvis.cli_ctl.discovery import discover

        found = discover()
        if found is not None and found.base_url:
            return str(found.base_url).rstrip("/")
    except Exception:  # noqa: BLE001 — nothing discovered is normal, not an error
        log.debug("agent MCP bridge: no running instance discovered", exc_info=True)
    port = _DEFAULT_PORT
    try:
        from jarvis.core.config import load_config

        port = int(load_config().ui.admin_api_port)
    except Exception:  # noqa: BLE001 — an unreadable config falls back to the shipped port
        log.debug("agent MCP bridge: config unreadable, using the default port", exc_info=True)
    return f"http://127.0.0.1:{port}"


def control_key() -> str | None:
    """The credential to present, best first.

    1. ``JARVIS_MCP_TOKEN`` — a per-client token. What a paired config carries,
       and the only one of the three that can be revoked on its own.
    2. ``JARVIS_CONTROL_KEY`` — the master key, for a bridge run by hand.
    3. the app's own lookup (keyring → env → file), so a bridge on the owner's
       machine needs no configuration at all.

    The name is historical; it returns whatever bearer value to send.
    """
    token = os.environ.get("JARVIS_MCP_TOKEN", "").strip()
    if token:
        return token
    from_env = os.environ.get("JARVIS_CONTROL_KEY", "").strip()
    if from_env:
        return from_env
    try:
        from jarvis.core import control_key as ck

        return ck.get_control_key()
    except Exception:  # noqa: BLE001 — no key found is a refusal we explain, not a crash
        log.debug("agent MCP bridge: control key unavailable", exc_info=True)
        return None


@contextlib.asynccontextmanager
async def _upstream() -> AsyncIterator[Any]:
    """One initialized client session against the HTTP surface.

    Opened per request rather than held open: the surface is stateless, so a
    fresh transport costs one round trip and buys us a bridge that survives
    Jarvis restarting underneath it — the case that actually happens, because
    the client outlives the app.
    """
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    key = control_key()
    if not key:
        raise RuntimeError(
            "No Jarvis credential found. Pair this client from the Jarvis app to "
            "get its own token, or set JARVIS_MCP_TOKEN / JARVIS_CONTROL_KEY."
        )
    url = f"{api_base_url()}{SURFACE_PATH}"
    headers = {"Authorization": f"Bearer {key}"}
    async with streamablehttp_client(url, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


def _offline_note(exc: Exception) -> str:
    return (
        "Jarvis is not reachable right now, so its agents cannot be listed or "
        f"talked to ({type(exc).__name__}: {exc}). Start the Personal Jarvis app "
        f"(expected at {api_base_url()}) and try again."
    )


def build_bridge_server() -> Any:
    """A stdio MCP server that forwards to the HTTP agent surface."""
    import mcp.types as types
    from mcp.server.lowlevel import Server

    server: Any = Server("jarvis-agents")

    @server.list_tools()  # type: ignore[misc, no-untyped-call]
    async def _list_tools() -> list[Any]:
        try:
            async with _upstream() as session:
                listed = await session.list_tools()
        except Exception as exc:  # noqa: BLE001 — an offline app is a message, not a failed server
            log.warning("agent MCP bridge: tool listing failed: %s", exc)
            return [
                types.Tool(
                    name="jarvis_unavailable",
                    description=_offline_note(exc),
                    inputSchema={"type": "object", "properties": {}},
                )
            ]
        return list(listed.tools)

    @server.call_tool()  # type: ignore[misc, no-untyped-call]
    async def _call_tool(name: str, arguments: dict[str, Any] | None) -> list[Any]:
        if name == "jarvis_unavailable":
            return [types.TextContent(type="text", text=_offline_note(RuntimeError("not running")))]
        try:
            async with _upstream() as session:
                result = await session.call_tool(
                    name, dict(arguments or {}), read_timeout_seconds=None
                )
        except Exception as exc:  # noqa: BLE001 — same
            log.warning("agent MCP bridge: %s failed: %s", name, exc)
            return [types.TextContent(type="text", text=_offline_note(exc))]
        return list(result.content)

    @server.list_resources()  # type: ignore[misc, no-untyped-call]
    async def _list_resources() -> list[Any]:
        try:
            async with _upstream() as session:
                listed = await session.list_resources()
        except Exception as exc:  # noqa: BLE001 — an offline app simply exposes nothing
            log.debug("agent MCP bridge: resource listing failed: %s", exc)
            return []
        return list(listed.resources)

    @server.read_resource()  # type: ignore[misc, no-untyped-call]
    async def _read_resource(uri: Any) -> str:
        try:
            async with _upstream() as session:
                result = await session.read_resource(uri)
        except Exception as exc:  # noqa: BLE001 — same
            return _offline_note(exc)
        parts = [getattr(c, "text", "") for c in result.contents]
        return "\n".join(p for p in parts if p)

    @server.list_prompts()  # type: ignore[misc, no-untyped-call]
    async def _list_prompts() -> list[Any]:
        try:
            async with _upstream() as session:
                listed = await session.list_prompts()
        except Exception as exc:  # noqa: BLE001 — same
            log.debug("agent MCP bridge: prompt listing failed: %s", exc)
            return []
        return list(listed.prompts)

    @server.get_prompt()  # type: ignore[misc, no-untyped-call]
    async def _get_prompt(name: str, arguments: dict[str, str] | None) -> Any:
        async with _upstream() as session:
            return await session.get_prompt(name, dict(arguments or {}))

    return server


async def serve_stdio() -> None:
    """Run the bridge on stdin/stdout until the client closes it."""
    from mcp.server.stdio import stdio_server

    server = build_bridge_server()
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main() -> int:
    import asyncio

    # stdout is the protocol channel — every log line must go to stderr or the
    # client sees corrupted JSON-RPC and drops the connection.
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    try:
        asyncio.run(serve_stdio())
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
