"""Turn-scoped Jarvis tools carried over an existing pinned SSH connection.

The remote machine receives an expiring capability, never the Control API key.
A loopback-only listener accepts that capability and delegates to the existing
MCP application with a fixed session. Catalog filtering, turn provenance and
ToolExecutor remain exactly the same as on a local CLI turn.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import socket
import time
from typing import Any

from jarvis.agent_chat.tool_context import required_turn_id, restore_turn
from jarvis.core.protocols import current_chat_turn

log = logging.getLogger(__name__)


class RemoteToolsUnavailable(RuntimeError):
    """The remote turn cannot reach its required Jarvis tools."""


class ScopedMcpApp:
    """An unforgeable, revocable reference to one active chat turn."""

    def __init__(self, session_id: str, turn_id: str, inner: Any, *, ttl: float = 3700) -> None:
        self.session_id = session_id
        self.turn_id = turn_id
        self.token = secrets.token_urlsafe(32)
        self.expires = time.monotonic() + ttl
        self.active = True
        self._inner = inner

    def revoke(self) -> None:
        self.active = False

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        from jarvis.core.control_key import get_control_key

        async def reject(status: int) -> None:
            await send(
                {
                    "type": "http.response.start",
                    "status": status,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": b'{"error":"Jarvis tool connection unavailable."}',
                }
            )

        if scope.get("type") != "http":
            return
        if scope.get("path") not in ("/mcp", "/mcp/") or scope.get("query_string"):
            await reject(404)
            return
        headers = dict(scope.get("headers") or ())
        presented = headers.get(b"authorization", b"")
        expected = ("Bearer " + self.token).encode("ascii")
        if (
            not self.active
            or time.monotonic() >= self.expires
            or not secrets.compare_digest(presented, expected)
        ):
            await reject(401)
            return
        with restore_turn(self.session_id):
            turn = current_chat_turn.get()
            if turn is None or turn.turn_id != self.turn_id:
                await reject(401)
                return
        # Neither a remote header nor a URL can select another chat, the agent
        # admin surface, or a REST endpoint. The owner key stays in this process.
        key = get_control_key()
        if not key:
            await reject(503)
            return
        forwarded = dict(scope)
        forwarded.update(
            path="/api/control/mcp/", raw_path=b"/api/control/mcp/", root_path="/api/control/mcp"
        )
        forwarded["headers"] = [
            (name, value)
            for name, value in scope.get("headers", ())
            if name not in (b"authorization", b"x-jarvis-chat-session")
        ] + [
            (b"authorization", ("Bearer " + key).encode("ascii")),
            (b"x-jarvis-chat-session", self.session_id.encode("ascii")),
        ]
        bound = required_turn_id.set(self.turn_id)
        try:
            await self._inner(forwarded, receive, send)
        finally:
            required_turn_id.reset(bound)


class RemoteMcpBridge:
    """The HTTP listener and reverse SSH forward belong to the CLI lifetime."""

    def __init__(self, app: ScopedMcpApp) -> None:
        self.app = app
        self.url = ""
        self._socket: socket.socket | None = None
        self._server: Any = None
        self._task: asyncio.Task[None] | None = None
        self._forward: Any = None

    @classmethod
    async def open(cls, connection: Any, session_id: str) -> RemoteMcpBridge:
        import uvicorn

        from jarvis.ui.web.mcp_server_routes import build_mcp_asgi_app

        with restore_turn(session_id):
            turn = current_chat_turn.get()
            if turn is None:
                raise RemoteToolsUnavailable(
                    "The Jarvis tool connection needs an active chat turn. Retry this message."
                )
        bridge = cls(ScopedMcpApp(session_id, turn.turn_id, build_mcp_asgi_app()))

        class ListenerServer(uvicorn.Server):
            @contextlib.contextmanager
            def capture_signals(self):  # type: ignore[no-untyped-def]
                # The desktop server owns process signals, not this child listener.
                yield

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            bridge._socket = sock
            sock.bind(("127.0.0.1", 0))
            sock.setblocking(False)
            bridge._server = ListenerServer(
                uvicorn.Config(
                    bridge.app,
                    lifespan="off",
                    access_log=False,
                    log_config=None,
                    log_level="warning",
                    timeout_graceful_shutdown=2,
                    ws="none",
                )
            )
            bridge._task = asyncio.create_task(
                bridge._server.serve(sockets=[sock]), name="remote-jarvis-tools"
            )
            async with asyncio.timeout(10):
                while not bridge._server.started:
                    if bridge._task.done():
                        await bridge._task
                        raise RemoteToolsUnavailable(
                            "Jarvis could not start the remote tool connection."
                        )
                    await asyncio.sleep(0.01)
            bridge._forward = await asyncio.wait_for(
                connection.forward_remote_port("127.0.0.1", 0, "127.0.0.1", sock.getsockname()[1]),
                timeout=20,
            )
            bridge.url = f"http://127.0.0.1:{bridge._forward.get_port()}/mcp"
            return bridge
        except BaseException as exc:
            await bridge.aclose()
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise RemoteToolsUnavailable(
                "The computer could not connect to Jarvis tools over SSH. "
                "Allow TCP forwarding in its SSH server settings, then retry."
            ) from exc

    async def aclose(self) -> None:
        self.app.revoke()
        try:
            if self._forward is not None:
                self._forward.close()
                await asyncio.wait_for(self._forward.wait_closed(), timeout=5)
        finally:
            if self._server is not None:
                self._server.should_exit = True
            if self._task is not None:
                try:
                    await asyncio.wait_for(self._task, timeout=5)
                except (TimeoutError, asyncio.CancelledError):
                    log.debug("remote tools: cancelling listener shutdown")
                    self._task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await self._task
            if self._socket is not None:
                self._socket.close()
