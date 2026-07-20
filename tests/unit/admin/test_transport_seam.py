"""AdminTransport seam (Wave 3, sub-task 3.1).

Proves the transport extraction: a signed envelope round-trips through the
reused HMAC core (``_decode_request`` -> executor -> ``_encode_response``) over a
``FakeAdminTransport`` with **no** real pipe/socket, the factory selects the
right per-OS transport and never raises, and the relocated Windows pipe code in
``NamedPipeTransport`` still exposes the SDDL helpers.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from jarvis.admin.executor import AdminExecutor
from jarvis.admin.ipc import AdminPipeClient, AdminPipeServer
from jarvis.admin.schema import ReadRegistryOp
from jarvis.admin.transport import (
    AdminTransport,
    NamedPipeTransport,
    make_admin_transport,
)
from tests.fakes.fake_admin_transport import FakeAdminTransport

SECRET = b"x" * 32


def test_make_admin_transport_returns_admin_transport():
    """The factory exits cleanly and returns an AdminTransport on this OS."""
    transport = make_admin_transport()
    assert isinstance(transport, AdminTransport)


def test_factory_never_raises_for_explicit_address():
    transport = make_admin_transport(r"\\.\pipe\jarvis-admin-test")
    assert isinstance(transport, AdminTransport)


def test_fake_transport_satisfies_protocol():
    assert isinstance(FakeAdminTransport(), AdminTransport)


def test_named_pipe_transport_satisfies_protocol():
    assert isinstance(NamedPipeTransport(r"\\.\pipe\x", sid="S-1-5-18"),
                      AdminTransport)


def test_named_pipe_transport_exposes_sddl_address():
    t = NamedPipeTransport(r"\\.\pipe\jarvis-admin-S-1-5-18", sid="S-1-5-18")
    assert t.address == r"\\.\pipe\jarvis-admin-S-1-5-18"


@pytest.mark.asyncio
async def test_signed_envelope_roundtrips_through_fake_transport():
    """A correctly-signed ReadRegistry op flows through the reused HMAC core.

    No real pipe/socket: ``FakeAdminTransport.roundtrip`` invokes the served
    ``AdminPipeServer.handle_raw`` handler directly.
    """
    transport = FakeAdminTransport()
    executor = AdminExecutor()
    server = AdminPipeServer(SECRET, r"\\.\pipe\test", executor,
                             sid="S-1-5-18", transport=transport)
    client = AdminPipeClient(SECRET, r"\\.\pipe\test", transport=transport)

    serve_task = asyncio.create_task(server.serve_forever())
    try:
        op = ReadRegistryOp(hive="HKCU", key_path="Environment",
                            value_name="PATH")
        resp = await client.send(op)
        # The round-trip infra is what we assert on; the registry read itself
        # may succeed or fail depending on the host (same contract as the
        # Windows loopback test).
        assert resp.op_id == op.op_id
        if not resp.success:
            assert resp.error_code in (
                "registry_key_not_found",
                "registry_read_failed",
                "registry_unsupported",
            )
        # The fake actually carried the raw signed envelope.
        assert transport.roundtrips, "transport.roundtrip was never invoked"
        envelope = json.loads(transport.roundtrips[0].decode("utf-8"))
        assert set(envelope) == {"nonce", "timestamp_ns", "hmac", "op"}
        assert envelope["op"]["type"] == "read_registry"
    finally:
        server.stop()
        await asyncio.wait_for(serve_task, timeout=2.0)


@pytest.mark.asyncio
async def test_tampered_envelope_rejected_through_transport():
    """A flipped HMAC is rejected by the reused core, even over the fake seam."""
    transport = FakeAdminTransport()
    server = AdminPipeServer(SECRET, r"\\.\pipe\test", AdminExecutor(),
                             sid="S-1-5-18", transport=transport)
    serve_task = asyncio.create_task(server.serve_forever())
    try:
        await asyncio.sleep(0)  # let serve register the handler
        op = ReadRegistryOp(hive="HKCU", key_path="Environment")
        client = AdminPipeClient(SECRET, r"\\.\pipe\test", transport=transport)
        envelope = client._build_envelope(op)
        envelope["hmac"] = "0" * 64  # tamper after signing
        raw = json.dumps(envelope).encode("utf-8")
        resp_bytes = await transport.roundtrip(raw)
        resp = json.loads(resp_bytes.decode("utf-8"))
        assert resp["success"] is False
        assert resp["error_code"] == "hmac_invalid"
    finally:
        server.stop()
        await asyncio.wait_for(serve_task, timeout=2.0)


@pytest.mark.asyncio
async def test_stop_propagates_to_transport():
    transport = FakeAdminTransport()
    server = AdminPipeServer(SECRET, r"\\.\pipe\test", AdminExecutor(),
                             sid="S-1-5-18", transport=transport)
    serve_task = asyncio.create_task(server.serve_forever())
    await asyncio.sleep(0)
    server.stop()
    await asyncio.wait_for(serve_task, timeout=2.0)
    assert transport.stop_calls >= 1
