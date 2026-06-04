"""AdminClient transport + elevator wiring (Wave 3, sub-task 3.6).

Proves the seams are wired without changing the execute control flow: the client
talks to whatever ``AdminTransport`` is injected, the elevation gate refuses with
the typed ``AdminResponse`` shape when the elevator is unavailable (AD-6 / the
extended ``no_secret`` refusal), and the destructive / cancel / event flow is
untouched. Uses ``FakeAdminTransport`` + ``FakeElevator`` — no real pipe/socket
and no real elevation prompt.
"""
from __future__ import annotations

import asyncio

import pytest

from jarvis.admin.client import AdminClient
from jarvis.admin.executor import AdminExecutor
from jarvis.admin.ipc import AdminPipeClient, AdminPipeServer
from jarvis.admin.schema import InstallWingetOp, ReadRegistryOp
from jarvis.core.bus import EventBus
from jarvis.core.events import AdminOperationRejected, AdminOperationRequested
from tests.fakes.fake_admin_transport import FakeAdminTransport
from tests.fakes.fake_elevator import FakeElevator

SECRET = b"x" * 32


def _appender(sink):
    async def _h(e):
        sink.append(e)
    return _h


@pytest.mark.asyncio
async def test_execute_routes_through_injected_transport():
    """A full send goes through the fake transport + reused HMAC core."""
    transport = FakeAdminTransport()
    server = AdminPipeServer(SECRET, r"\\.\pipe\test", AdminExecutor(),
                             sid="S-1-5-18", transport=transport)
    serve_task = asyncio.create_task(server.serve_forever())
    try:
        pipe_client = AdminPipeClient(SECRET, r"\\.\pipe\test",
                                      transport=transport)
        client = AdminClient(pipe_client=pipe_client,
                             elevator=FakeElevator(available=True))
        resp = await client.execute(
            ReadRegistryOp(hive="HKCU", key_path="Environment")
        )
        assert resp.op_id is not None
        assert transport.roundtrips, "request never reached the transport"
    finally:
        server.stop()
        await asyncio.wait_for(serve_task, timeout=2.0)


@pytest.mark.asyncio
async def test_null_elevator_refusal_is_typed_response(monkeypatch):
    """AD-6: no elevation -> AdminResponse(success=False, error_code='no_elevation').

    Not an injected pipe_client, so the elevation gate fires. We stub the secret
    so the no_secret path doesn't pre-empt the elevation check.
    """
    from jarvis.admin import client as client_mod

    monkeypatch.setattr(
        client_mod.AdminClient, "_load_secret", lambda self: SECRET
    )
    bus = EventBus()
    seen: list[object] = []
    bus.subscribe(AdminOperationRejected, _appender(seen))

    # No injected pipe_client -> _ensure_transport builds a real transport, but
    # the elevation gate refuses before any roundtrip is attempted.
    client = AdminClient(bus=bus, elevator=FakeElevator(available=False))
    resp = await client.execute(InstallWingetOp(package_id="7zip.7zip"))

    assert resp.success is False
    assert resp.error_code == "no_elevation"
    assert any(
        isinstance(e, AdminOperationRejected) and e.reason == "no_elevation"
        for e in seen
    )


@pytest.mark.asyncio
async def test_no_secret_still_refuses_when_secret_absent(monkeypatch):
    """The original no_secret refusal shape is preserved."""
    from jarvis.admin import client as client_mod

    monkeypatch.setattr(
        client_mod.AdminClient, "_load_secret", lambda self: None
    )
    client = AdminClient(elevator=FakeElevator(available=True))
    resp = await client.execute(InstallWingetOp(package_id="7zip.7zip"))
    assert resp.success is False
    assert resp.error_code == "no_secret"


@pytest.mark.asyncio
async def test_default_client_uses_make_elevator():
    """An AdminClient with no injected elevator gets one from the factory."""
    client = AdminClient()
    # On every OS make_elevator returns *some* Elevator; just assert it exists
    # and exposes the protocol surface.
    assert hasattr(client._elevator, "is_available")
    assert hasattr(client._elevator, "ensure_elevated_helper")


@pytest.mark.asyncio
async def test_injected_pipe_client_bypasses_elevation_gate():
    """An injected transport means elevation is provided out-of-band (tests)."""
    transport = FakeAdminTransport()
    server = AdminPipeServer(SECRET, r"\\.\pipe\test", AdminExecutor(),
                             sid="S-1-5-18", transport=transport)
    serve_task = asyncio.create_task(server.serve_forever())
    try:
        pipe_client = AdminPipeClient(SECRET, r"\\.\pipe\test",
                                      transport=transport)
        # Even with an UNAVAILABLE elevator, the injected pipe_client proceeds.
        client = AdminClient(pipe_client=pipe_client,
                             elevator=FakeElevator(available=False))
        resp = await client.execute(
            ReadRegistryOp(hive="HKCU", key_path="Environment")
        )
        assert resp.error_code != "no_elevation"
    finally:
        server.stop()
        await asyncio.wait_for(serve_task, timeout=2.0)
