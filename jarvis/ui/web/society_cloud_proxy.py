"""Route a hosted agent's existing UI to its owner, inside surface security."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
from pathlib import Path
from typing import Any

from starlette.requests import HTTPConnection, Request
from starlette.responses import JSONResponse
from starlette.websockets import WebSocket

log = logging.getLogger(__name__)
_AGENT = re.compile(r"^/api/society/agents/([^/]+)(?:/|$)")
_CHAT = re.compile(r"^/api/agent-chat/sessions/society:([^/:]+)(?::[^/]*)?(?:/|$)")
_MAX_BODY = 8 * 1024 * 1024


def cloud_agent_path(path: str) -> str | None:
    """Recognize only agent-owned routes, never arbitrary host or file paths."""
    match = _AGENT.match(path) or _CHAT.match(path)
    return match[1] if match else None


class SocietyCloudProxy:
    def __init__(self, app: Any, *, data_dir: str) -> None:
        self.app = app
        self.data_dir = Path(data_dir)

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        agent_id = cloud_agent_path(scope.get("path", ""))
        if agent_id is None:
            await self.app(scope, receive, send)
            return
        from jarvis.society.cloud_host import CloudHost, placement_for

        placement = placement_for(self.data_dir, agent_id)
        if placement is None:
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            raw_send = send
            closed = False

            async def send_once(message: dict[str, Any]) -> None:
                nonlocal closed
                if closed:
                    return
                if message["type"] == "websocket.close":
                    closed = True
                await raw_send(message)

            send = send_once
        if placement["state"] != "active":
            await self._unavailable(
                scope, receive, send, 409, "Cloud handoff needs to finish or be reconciled."
            )
            return
        from .society_routes import _runtime

        try:
            runtime = await _runtime(HTTPConnection(scope))
            host = CloudHost(runtime)
            if scope["type"] == "websocket":
                await self._stream(host, agent_id, scope, receive, send)
                return
            request = Request(scope, receive)
            chunks: list[bytes] = []
            size = 0
            async for chunk in request.stream():
                size += len(chunk)
                if size > _MAX_BODY:
                    await self._unavailable(scope, receive, send, 413, "Request too large")
                    return
                chunks.append(chunk)
            raw = b"".join(chunks)
            try:
                body = json.loads(raw) if raw else None
            except (ValueError, UnicodeError):
                # The caller receives an explicit unsupported-content response.
                await self._unavailable(scope, receive, send, 415, "Expected a JSON request")
                return
            path = scope["path"]
            query = scope.get("query_string", b"").decode("ascii")
            if query:
                path += "?" + query
            status, payload = await host.request(agent_id, scope["method"], path, body)
            if 200 <= status < 300 and isinstance(payload, dict):
                remote_agent = payload.get("agent")
                if isinstance(remote_agent, dict) and remote_agent.get("agent_id") == agent_id:
                    remote_agent = dict(remote_agent)
                    if remote_agent.get("state") == "paused":
                        remote_agent["run_state"] = "paused"
                    elif isinstance(payload.get("active_runs"), int):
                        remote_agent["run_state"] = "working" if payload["active_runs"] else "idle"
                    await host.remember_agent(agent_id, remote_agent)
            await JSONResponse(payload, status_code=status)(scope, receive, send)
        except Exception as exc:
            # Do not expose transport messages: an upstream URL may contain credentials.
            log.warning("Cloud connection for %s failed (%s)", agent_id, type(exc).__name__)
            await self._unavailable(
                scope,
                receive,
                send,
                502,
                "The cloud host is unreachable. Local execution stays disabled.",
            )

    @staticmethod
    async def _unavailable(scope: Any, receive: Any, send: Any, status: int, detail: str) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1013, "reason": detail[:120]})
        else:
            await JSONResponse({"detail": detail}, status_code=status)(scope, receive, send)

    @staticmethod
    async def _stream(host: Any, agent_id: str, scope: Any, receive: Any, send: Any) -> None:
        from websockets.asyncio.client import connect

        ws = WebSocket(scope, receive, send)
        async with host.tunnel(agent_id) as (base_url, token):
            query = scope.get("query_string", b"").decode("ascii")
            url = base_url.replace("http://", "ws://", 1) + scope["path"]
            if query:
                url += "?" + query
            async with connect(
                url,
                additional_headers={"Authorization": f"Bearer {token}"},
                open_timeout=15,
                max_size=_MAX_BODY,
            ) as remote:
                await ws.accept()

                async def upstream() -> None:
                    while True:
                        message = await ws.receive()
                        if message["type"] == "websocket.disconnect":
                            return
                        if message.get("text") is not None:
                            await remote.send(message["text"])
                        elif message.get("bytes") is not None:
                            await remote.send(message["bytes"])

                async def downstream() -> None:
                    async for message in remote:
                        if isinstance(message, str):
                            await ws.send_text(message)
                        else:
                            await ws.send_bytes(message)

                pending = {asyncio.create_task(upstream()), asyncio.create_task(downstream())}
                close_code = 1000
                try:
                    done, _ = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                    for task in done:
                        task.result()
                except Exception:
                    close_code = 1011
                    raise
                finally:
                    for task in pending:
                        task.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
                    with contextlib.suppress(RuntimeError, OSError):
                        await ws.close(code=close_code)
