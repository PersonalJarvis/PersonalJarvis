"""AF_UNIX loopback for the admin transport (Wave 3, sub-task 3.2).

The POSIX mirror of ``tests/integration/test_admin_ipc_loopback.py``: a signed
envelope round-trips through a real ``UnixSocketTransport`` + the reused
``AdminPipeServer.handle_raw`` chain (``_decode_request`` -> executor ->
``_encode_response``), with no elevation and no interactive prompt.

AF_UNIX is POSIX-only, so the whole module is ``skipif win32``. Unlike the
Windows named-pipe loopback (which is ``skip_ci`` because pywin32 is desktop-
only), an AF_UNIX socket runs fine on a Linux/macOS CI runner — this test is the
standout cross-platform verification win for the Admin wave and is intentionally
NOT marked ``skip_ci``.
"""
from __future__ import annotations

import asyncio
import sys

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="AF_UNIX domain sockets are POSIX-only; runs on Linux/macOS CI.",
)

if sys.platform != "win32":
    from jarvis.admin.executor import AdminExecutor
    from jarvis.admin.ipc import AdminPipeClient, AdminPipeServer
    from jarvis.admin.schema import ReadRegistryOp
    from jarvis.admin.unix_socket import UnixSocketTransport


@pytest.mark.asyncio
async def test_signed_envelope_roundtrips_over_unix_socket(tmp_path):
    secret = b"X" * 32
    sock_path = str(tmp_path / "jarvis-admin-loopback.sock")
    pipe_name = sock_path  # the "address" the HMAC core does not care about

    executor = AdminExecutor()
    server_transport = UnixSocketTransport(sock_path)
    server = AdminPipeServer(
        secret, pipe_name, executor, sid="S-0-0", transport=server_transport
    )
    client = AdminPipeClient(
        secret, pipe_name, io_timeout_s=10.0,
        transport=UnixSocketTransport(sock_path),
    )

    serve_task = asyncio.create_task(server.serve_forever())
    try:
        # Wait for the socket to be bound.
        import os
        for _ in range(200):
            if os.path.exists(sock_path):
                break
            await asyncio.sleep(0.01)

        # On a non-Windows host the registry op is not executable; we are only
        # proving the round-trip + HMAC + peer-cred path. A read_registry op
        # decodes + validates fine, then the executor returns a typed failure.
        op = ReadRegistryOp(hive="HKCU", key_path="Environment",
                            value_name="PATH")
        resp = await client.send(op)
        assert resp.op_id == op.op_id
        # The peer is the same uid (this process talks to itself), so the
        # peer-cred gate accepts and the HMAC core decodes — success or a typed
        # executor failure, never an auth rejection.
        if not resp.success:
            assert resp.error_code not in ("hmac_invalid", "nonce_replay")
    finally:
        server.stop()
        await asyncio.wait_for(serve_task, timeout=5.0)


@pytest.mark.asyncio
async def test_tampered_hmac_rejected_over_unix_socket(tmp_path):
    import json

    secret = b"X" * 32
    sock_path = str(tmp_path / "jarvis-admin-tamper.sock")
    server = AdminPipeServer(
        secret, sock_path, AdminExecutor(), sid="S-0-0",
        transport=UnixSocketTransport(sock_path),
    )
    serve_task = asyncio.create_task(server.serve_forever())
    try:
        import os
        for _ in range(200):
            if os.path.exists(sock_path):
                break
            await asyncio.sleep(0.01)

        client = AdminPipeClient(
            secret, sock_path, transport=UnixSocketTransport(sock_path)
        )
        op = ReadRegistryOp(hive="HKCU", key_path="Environment")
        envelope = client._build_envelope(op)
        envelope["hmac"] = "0" * 64
        raw = json.dumps(envelope).encode("utf-8")
        resp_bytes = await client._ensure_transport().roundtrip(raw)
        resp = json.loads(resp_bytes.decode("utf-8"))
        assert resp["success"] is False
        assert resp["error_code"] == "hmac_invalid"
    finally:
        server.stop()
        await asyncio.wait_for(serve_task, timeout=5.0)
