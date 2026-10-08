"""Relay native Claude tool approvals from an owned OpenClaw Gateway to ACP's host.

OpenClaw's ACP bridge already relays exec approvals. Native Claude file and
extension tools use the separate ``plugin.approval`` Gateway protocol. This
subscriber exists only during one turn and accepts only that turn's observed
tool calls. It never reconnects or selects another permission policy.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import sys
import time
import uuid
from typing import Any

from jarvis.agent_runtimes.acp import AcpIO, AcpTurn

log = logging.getLogger(__name__)
_READ_ONLY = frozenset({"read", "grep", "glob"})
_RPC_TIMEOUT = 5.0
_CORRELATION_TIMEOUT = 5.0
_MAX_PENDING = 32


class OpenClawApprovals:
    """One loopback connection authenticated with this agent's Gateway token."""

    def __init__(self, port: int, token: str, session_key: str) -> None:
        self._url = f"ws://127.0.0.1:{port}"
        self._token = token
        self._session_key = session_key
        self._agent_id = session_key.split(":", 2)[1]
        self._socket: Any = None
        self._reader: asyncio.Task[None] | None = None
        self._turn: AcpTurn | None = None
        self._io: AcpIO | None = None
        self._started_ms = 0
        self._closing = False
        self._failed = False
        self._tools: dict[str, tuple[str, dict[str, Any], str]] = {}
        self._tool_available = asyncio.Event()
        self._pending: dict[str, asyncio.Task[None]] = {}
        self._requests: dict[str, asyncio.Future[Any]] = {}
        self._seen: set[str] = set()
        self._deciding: set[str] = set()

    async def start(self, turn: AcpTurn, io: AcpIO) -> None:
        if self._reader is not None:
            if self._failed or self._closing:
                raise RuntimeError("OpenClaw approval connection is no longer available")
            return
        from websockets.asyncio.client import connect

        self._turn, self._io = turn, io
        self._started_ms = int(time.time() * 1000)
        options: dict[str, Any] = {
            "open_timeout": 10, "close_timeout": 2, "max_size": 256 * 1024,
        }
        # Newer websockets versions discover proxies automatically. This
        # connection belongs to our loopback child, never a network proxy.
        if "proxy" in inspect.signature(connect).parameters:
            options["proxy"] = None
        try:
            self._socket = await connect(self._url, **options)
            challenge = json.loads(await asyncio.wait_for(self._socket.recv(), 10))
            if challenge.get("event") != "connect.challenge":
                raise RuntimeError("OpenClaw did not send its Gateway challenge")
            request_id = uuid.uuid4().hex
            await self._socket.send(json.dumps({
                "type": "req", "id": request_id, "method": "connect", "params": {
                    "minProtocol": 4, "maxProtocol": 4,
                    "client": {
                        "id": "gateway-client", "displayName": "Jarvis approvals",
                        "version": "1", "platform": sys.platform, "mode": "backend",
                    },
                    "role": "operator",
                    # This is Jarvis' isolated, token-authenticated Gateway.
                    # Its owner scope can see native approvals requested by
                    # the separate Claude transport connection.
                    "scopes": ["operator.admin", "operator.approvals"],
                    "caps": ["approvals", "plugin-approvals"],
                    "auth": {"token": self._token},
                },
            }))
            response = json.loads(await asyncio.wait_for(self._socket.recv(), 10))
            if response.get("id") != request_id or response.get("ok") is not True:
                raise RuntimeError("OpenClaw refused the approval connection")
            self._reader = asyncio.create_task(self._read())
        except BaseException:
            await self.close()
            raise

    def note_tool(self, call_id: str, name: str, args: dict[str, Any], summary: str) -> None:
        self._tools[call_id] = (name, dict(args), summary)
        self._tool_available.set()

    async def _rpc(self, method: str, params: dict[str, Any]) -> Any:
        if self._socket is None or self._failed:
            raise RuntimeError("OpenClaw approval connection is unavailable")
        request_id = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self._requests[request_id] = future
        try:
            await self._socket.send(json.dumps({
                "type": "req", "id": request_id, "method": method, "params": params,
            }))
            return await asyncio.wait_for(future, _RPC_TIMEOUT)
        finally:
            self._requests.pop(request_id, None)

    async def _read(self) -> None:
        try:
            async for raw in self._socket:
                event = json.loads(raw)
                if not isinstance(event, dict):
                    raise ValueError("Invalid Gateway frame")
                if event.get("type") == "res":
                    future = self._requests.get(str(event.get("id") or ""))
                    if future is not None and not future.done():
                        if event.get("ok") is True:
                            future.set_result(event.get("payload"))
                        else:
                            future.set_exception(RuntimeError("OpenClaw refused an approval reply"))
                    continue
                payload = event.get("payload")
                if not isinstance(payload, dict):
                    continue
                if event.get("event") == "plugin.approval.requested":
                    self._requested(payload)
                elif event.get("event") == "plugin.approval.resolved":
                    approval_id = str(payload.get("id") or "")
                    if approval_id in self._deciding:
                        continue
                    task = self._pending.pop(approval_id, None)
                    if task is not None:
                        task.cancel()
            if not self._closing:
                await self._abort()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("OpenClaw approval transport stopped (%s)", type(exc).__name__)
            await self._abort()

    def _requested(self, payload: dict[str, Any]) -> None:
        turn = self._turn
        if self._closing or self._failed or turn is None or turn.saw_result:
            return
        request = payload.get("request")
        if not isinstance(request, dict):
            return
        approval_id = str(payload.get("id") or "")
        created = payload.get("createdAtMs")
        expires = payload.get("expiresAtMs")
        if (
            not approval_id or approval_id in self._seen
            or request.get("sessionKey") != self._session_key
            or request.get("agentId") != self._agent_id
            # Native plugin approvals on supported OpenClaw releases can
            # omit runId. ACP already selects tool events by its active run;
            # that exact toolCallId below supplies our turn binding.
            or not request.get("toolCallId")
            or not isinstance(created, int | float) or created < self._started_ms
            or not isinstance(expires, int | float) or expires <= time.time() * 1000
        ):
            return
        if len(self._pending) >= _MAX_PENDING or len(self._seen) >= 4096:
            task = asyncio.create_task(self._abort())
            task.add_done_callback(_observe_task)
            return
        self._seen.add(approval_id)
        task = asyncio.create_task(self._answer(approval_id, request, float(expires)))
        self._pending[approval_id] = task
        task.add_done_callback(_observe_task)

    async def _answer(self, approval_id: str, request: dict[str, Any], expires: float) -> None:
        try:
            call_id = str(request["toolCallId"])
            remaining = max(0.0, (expires - time.time() * 1000) / 1000)
            async with asyncio.timeout(min(_CORRELATION_TIMEOUT, remaining)):
                while call_id not in self._tools:
                    self._tool_available.clear()
                    await self._tool_available.wait()
            turn, io = self._turn, self._io
            if turn is None or io is None or turn.saw_result or self._closing:
                return
            name, args, summary = self._tools[call_id]
            if turn.auto_deny:
                # Both transports must identify this as a known read-only
                # native tool. Unknown extensions and all shell calls deny.
                read_only = (
                    name.lower() in _READ_ONLY
                    and str(request.get("toolName")).lower() in _READ_ONLY
                )
                decision = "allow" if read_only else "deny"
            else:
                remaining = max(0.0, (expires - time.time() * 1000) / 1000)
                try:
                    async with asyncio.timeout(min(remaining, 300.0)):
                        decision = await io.ask(call_id, name, args, summary)
                except TimeoutError:
                    decision = "deny"
            if turn.saw_result or self._closing:
                return
            reply = {"allow": "allow-once", "allow_always": "allow-always"}.get(decision, "deny")
            allowed = request.get("allowedDecisions")
            if isinstance(allowed, list) and reply not in allowed:
                reply = (
                    "allow-once" if reply == "allow-always" and "allow-once" in allowed else "deny"
                )
            self._deciding.add(approval_id)
            await self._rpc("plugin.approval.resolve", {"id": approval_id, "decision": reply})
            if decision == "cancel":
                frame = turn.cancel_frame()
                if frame is not None:
                    await io.write(frame)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("OpenClaw approval could not complete (%s)", type(exc).__name__)
            await self._abort()
        finally:
            self._deciding.discard(approval_id)
            self._pending.pop(approval_id, None)

    async def _abort(self) -> None:
        if self._closing or self._failed:
            return
        self._failed = True
        for future in self._requests.values():
            if not future.done():
                future.set_exception(RuntimeError("OpenClaw approval connection was lost"))
        for task in tuple(self._pending.values()):
            if task is not asyncio.current_task():
                task.cancel()
        if self._turn is not None and self._io is not None:
            await self._turn.approval_failed(self._io)

    async def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        pending = tuple(self._pending.items())
        for _, task in pending:
            task.cancel()
        await asyncio.gather(*(task for _, task in pending), return_exceptions=True)
        if self._reader is not None and not self._failed:
            # A cancelled card must not leave a request waiting on the vendor
            # until expiry. The reader remains alive for these bounded replies.
            replies = [
                self._rpc("plugin.approval.resolve", {"id": key, "decision": "deny"})
                for key, _ in pending
            ]
            if replies:
                results = await asyncio.gather(*replies, return_exceptions=True)
                if any(isinstance(result, BaseException) for result in results):
                    log.info("OpenClaw pending approvals ended while closing the turn")
        if self._socket is not None:
            await self._socket.close()
        if self._reader is not None:
            self._reader.cancel()
            await asyncio.gather(self._reader, return_exceptions=True)
        self._pending.clear()
        self._tools.clear()


def _observe_task(task: asyncio.Task[None]) -> None:
    if not task.cancelled() and task.exception() is not None:
        log.warning("OpenClaw approval cleanup failed (%s)", type(task.exception()).__name__)
