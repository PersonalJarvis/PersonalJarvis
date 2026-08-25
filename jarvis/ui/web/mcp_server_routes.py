"""Serve Jarvis' own tools over MCP, so a spawned agent session can drive them.

Mounted as a raw ASGI sub-app rather than a FastAPI router: the MCP Streamable
HTTP transport needs the untouched ``(scope, receive, send)`` triple — it
streams, negotiates its own content types and answers ``GET``/``DELETE`` as
well as ``POST``. Wrapping that in a response model would fight it.

Authentication is the Control API's Bearer key, checked here instead of by a
dependency for the same reason. The rule is the Control API's rule (see
``control_auth``): loopback does NOT bypass it. The key is exactly what makes
this surface safe to exist — anything on the machine could otherwise open a
socket and start driving the person's calendar.

The session manager is stateless (a fresh transport per request), so a chat
turn that dies mid-tool leaves nothing to clean up. Its task group is started
on first use and lives for the process — there is no lifespan hook on this app
to hang it from, and one background task is cheaper than a per-request one.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

log = logging.getLogger(__name__)

_UNAUTHORIZED_BODY = b'{"error":"Invalid or missing Jarvis Control API key."}'

_manager: Any | None = None
_manager_ready: asyncio.Event | None = None
_manager_task: asyncio.Task[None] | None = None


def _bearer(scope: dict[str, Any]) -> str | None:
    for raw_name, raw_value in scope.get("headers") or ():
        if raw_name == b"authorization":
            scheme, _, token = raw_value.decode("latin-1").partition(" ")
            if scheme.lower() == "bearer" and token.strip():
                return token.strip()
    return None


#: The agent-chat session header, lower-cased as ASGI hands headers over.
_SESSION_HEADER = b"x-jarvis-chat-session"


def session_ref(scope: dict[str, Any]) -> str | None:
    """The chat session id a request names, or ``None`` (any other client)."""
    for raw_name, raw_value in scope.get("headers") or ():
        if raw_name == _SESSION_HEADER:
            value = raw_value.decode("latin-1").strip()
            # A session id is a hex uuid; anything else is not ours to trust.
            if value and len(value) <= 64 and value.replace("-", "").isalnum():
                return value
    return None


async def _reject(send: Any, status: int, body: bytes) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


async def _run_manager(manager: Any, ready: asyncio.Event) -> None:
    """Hold the manager's task group open for the life of the process."""
    try:
        async with manager.run():
            ready.set()
            await asyncio.Event().wait()  # never set — cancelled at shutdown
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 — a dead manager must not take the web server with it
        log.warning("jarvis MCP: session manager stopped", exc_info=True)
    finally:
        ready.set()  # unblock waiters so they fail fast instead of hanging


async def _ensure_manager() -> Any | None:
    global _manager, _manager_ready, _manager_task
    if _manager is not None and _manager_ready is not None:
        await _manager_ready.wait()
        return _manager
    try:
        from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

        from jarvis.mcp.jarvis_tools_server import build_server
    except Exception:  # noqa: BLE001 — no MCP library → the surface is simply absent
        log.warning("jarvis MCP: server unavailable", exc_info=True)
        return None
    manager = StreamableHTTPSessionManager(
        app=build_server(),
        json_response=True,
        stateless=True,
    )
    ready = asyncio.Event()
    _manager = manager
    _manager_ready = ready
    _manager_task = asyncio.create_task(_run_manager(manager, ready))
    await ready.wait()
    return manager


def build_mcp_asgi_app() -> Any:
    """The ASGI app to mount at ``/api/control/mcp``."""

    async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            return
        from jarvis.core import control_key as ck

        if not ck.verify_control_key(_bearer(scope)):
            await _reject(send, 401, _UNAUTHORIZED_BODY)
            return
        manager = await _ensure_manager()
        if manager is None:
            await _reject(send, 503, b'{"error":"Jarvis MCP server is not available."}')
            return
        # The session travels in a ContextVar: the stateless transport runs
        # the tool call in a task started from this request, which inherits
        # the context, and the tool server reads it when it builds the
        # executor's approval surface (jarvis_tools_server.approval_snapshot).
        from jarvis.mcp.jarvis_tools_server import CHAT_SESSION_REF

        token = CHAT_SESSION_REF.set(session_ref(scope))
        try:
            await manager.handle_request(scope, receive, send)
        finally:
            CHAT_SESSION_REF.reset(token)

    return app


__all__ = ["build_mcp_asgi_app", "session_ref"]
