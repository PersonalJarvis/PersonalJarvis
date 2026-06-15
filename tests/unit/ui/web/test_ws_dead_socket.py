"""Regression: the WS receive loop must not spin forever on a dead socket.

Live incident 2026-06-14: when a WebSocket client disconnected uncleanly,
``ws.receive_json()`` raised ``RuntimeError('WebSocket is not connected ...')``
instead of ``WebSocketDisconnect``. The handler's ``except Exception: ... continue``
then re-called ``receive_json`` on the closed socket indefinitely, writing the
traceback at ~9 MB/s (the log rotated three 9.7 MB files in two seconds),
wedging the event loop and triggering a self-restart that cancelled every
in-flight sub-agent mission with ``app_shutdown``.

The fix: a dead-socket ``RuntimeError`` ends the loop (``break``) like a
disconnect; only genuinely recoverable malformed-frame errors ``continue``.
"""
from __future__ import annotations

import asyncio

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.ui.web.server import WebServer


class _LoopGuard(BaseException):
    """Raised after too many recv calls; BaseException so it escapes the
    handler's ``except Exception`` and proves the loop never terminated."""


class _DeadSocketWS:
    """Fake WebSocket that fails every ``receive_json`` like a closed socket."""

    def __init__(self, cap: int = 5) -> None:
        self.recv_calls = 0
        self.cap = cap
        self.closed = False

    async def accept(self) -> None:  # noqa: D401
        return None

    async def send_json(self, *_a: object, **_k: object) -> None:
        return None

    async def receive_json(self) -> object:
        self.recv_calls += 1
        if self.recv_calls > self.cap:
            raise _LoopGuard()
        raise RuntimeError('WebSocket is not connected. Need to call "accept" first.')

    async def close(self) -> None:
        self.closed = True


async def test_handle_ws_breaks_on_dead_socket_does_not_loop() -> None:
    srv = WebServer(JarvisConfig(), bus=EventBus())
    ws = _DeadSocketWS(cap=5)
    try:
        await asyncio.wait_for(srv._handle_ws(ws), timeout=5.0)
    except _LoopGuard:
        pytest.fail(
            f"_handle_ws looped on a dead socket: receive_json called "
            f"{ws.recv_calls}x (expected exactly 1 then break)"
        )
    assert ws.recv_calls == 1, (
        f"receive_json should be called once then break, got {ws.recv_calls}"
    )
    assert ws.closed is True
