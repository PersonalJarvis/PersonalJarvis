"""Regression: the WS receive loop must not spin forever on a dead socket.

Live incident 2026-06-14: when a WebSocket client disconnected uncleanly,
``ws.receive_json()`` raised ``RuntimeError('WebSocket is not connected ...')``
instead of ``WebSocketDisconnect``. Retrying any receive failure then re-called
``receive_json`` on the closed socket indefinitely, writing the
traceback at ~9 MB/s (the log rotated three 9.7 MB files in two seconds),
wedging the event loop and triggering a self-restart that cancelled every
in-flight sub-agent mission with ``app_shutdown``.

The fix: every receive error ends the loop. The handler may report it
best-effort, but never reads from a socket whose state is uncertain.
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

    def __init__(self, error: Exception, cap: int = 5) -> None:
        self.recv_calls = 0
        self.cap = cap
        self.closed = False
        self.error = error

    async def accept(self) -> None:  # noqa: D401
        return None

    async def send_json(self, *_a: object, **_k: object) -> None:
        return None

    async def receive_json(self) -> object:
        self.recv_calls += 1
        if self.recv_calls > self.cap:
            raise _LoopGuard()
        raise self.error

    async def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError('WebSocket is not connected. Need to call "accept" first.'),
        ValueError("malformed JSON frame"),
    ],
)
async def test_handle_ws_breaks_on_any_receive_error_does_not_loop(
    error: Exception,
) -> None:
    srv = WebServer(JarvisConfig(), bus=EventBus())
    ws = _DeadSocketWS(error, cap=5)
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
