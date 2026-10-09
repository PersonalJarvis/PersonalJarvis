"""Content-free WebSocket transport and upgrade milestones use real lifecycle hooks."""

from __future__ import annotations

import asyncio
from http import HTTPStatus
from types import SimpleNamespace

import pytest

from jarvis.plugins.realtime._live_transport import startup_websocket_options
from tests.fakes.fake_subscription_live_wire import (
    FakeSubscriptionLiveWire,
    FakeSubscriptionSocket,
)


async def test_real_socket_separates_transport_from_delayed_upgrade():
    from websockets.asyncio.client import connect
    from websockets.asyncio.server import serve

    phases = []
    request_received = asyncio.Event()
    release_upgrade = asyncio.Event()

    async def process_request(connection, request):
        request_received.set()
        await release_upgrade.wait()

    async def handler(socket):
        await socket.wait_closed()

    async with serve(handler, "127.0.0.1", 0, process_request=process_request) as server:
        port = server.sockets[0].getsockname()[1]

        async def open_socket():
            return await connect(
                f"ws://127.0.0.1:{port}/private-path",
                additional_headers={"Authorization": "private-header"},
                **startup_websocket_options(phases.append),
            )

        pending = asyncio.create_task(open_socket())
        try:
            await asyncio.wait_for(request_received.wait(), 2)
            assert phases == ["control_transport_connected", "control_handshake_started"]
            assert not pending.done()
            release_upgrade.set()
            socket = await asyncio.wait_for(pending, 2)
            await socket.close()
            assert phases == [
                "control_transport_connected", "control_handshake_started",
                "control_handshake_complete",
            ]
        finally:
            release_upgrade.set()
            if not pending.done():
                pending.cancel()
            results = await asyncio.gather(pending, return_exceptions=True)
            if not isinstance(results[0], BaseException):
                await results[0].close()


async def test_rejected_upgrade_never_reports_handshake_complete():
    from websockets.asyncio.client import connect
    from websockets.asyncio.server import serve
    from websockets.exceptions import InvalidStatus

    phases = []

    def process_request(connection, request):
        return connection.respond(HTTPStatus.UNAUTHORIZED, "private-response")

    async def handler(socket):
        pytest.fail("Rejected clients must never reach the connection handler")

    async with serve(handler, "127.0.0.1", 0, process_request=process_request) as server:
        port = server.sockets[0].getsockname()[1]
        with pytest.raises(InvalidStatus):
            await connect(
                f"ws://127.0.0.1:{port}/private-path",
                **startup_websocket_options(phases.append),
            )
    assert phases == ["control_transport_connected", "control_handshake_started"]


async def test_subscription_injected_connector_receives_startup_hook():
    from websockets.asyncio.client import ClientConnection

    wire = FakeSubscriptionLiveWire()
    phases = []
    config = wire.config()
    config.on_startup_phase = phases.append
    connection = await wire.provider().open_session(config)
    try:
        assert len(wire.connects) == 1
        assert "control_connect_started" in phases
        assert issubclass(wire.connects[0][1]["create_connection"], ClientConnection)
        # A fake connector did not complete a transport or a handshake.
        assert "control_transport_connected" not in phases
        assert "control_handshake_complete" not in phases
    finally:
        await connection.close()


async def test_public_live_injected_connector_receives_startup_hook(monkeypatch):
    from websockets.asyncio import client

    from jarvis.plugins.realtime.openai_live import OpenAILiveProvider

    phases, connects = [], []
    socket = FakeSubscriptionSocket()

    async def connect(url, **kwargs):
        connects.append(kwargs)
        return socket

    monkeypatch.setattr(client, "connect", connect)
    config = SimpleNamespace(session={}, offer_sdp="", on_startup_phase=phases.append)
    connection = await OpenAILiveProvider(api_key="fake-key").open_session(config)
    try:
        assert len(connects) == 1
        assert "control_connect_started" in phases
        assert issubclass(connects[0]["create_connection"], client.ClientConnection)
        assert "control_handshake_complete" not in phases
        assert socket.sent[0]["type"] == "session.start"
    finally:
        await connection.close()
