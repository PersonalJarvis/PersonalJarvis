"""IPC loopback test: AdminPipeServer + AdminPipeClient in the same process.

No real UAC prompt — we start the server in the pytest event loop and
send a harmless ``read_registry`` op against ``HKCU\\Environment``.

The test is ``@pytest.mark.phase5 + @pytest.mark.skip_ci`` because it
needs Windows named pipes (pywin32). Runs locally on the dev machine.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

from jarvis.admin.executor import AdminExecutor
from jarvis.admin.ipc import AdminPipeClient, AdminPipeServer
from jarvis.admin.schema import ReadRegistryOp

pytestmark = [pytest.mark.phase5, pytest.mark.skip_ci]


@pytest.mark.asyncio
async def test_read_registry_roundtrip():
    try:
        import win32pipe  # type: ignore[import-not-found,unused-import]  # noqa: F401
    except ImportError:
        pytest.skip("pywin32 not available")

    secret = b"X" * 32
    pipe_name = rf"\\.\pipe\jarvis-admin-test-{uuid.uuid4().hex}"
    executor = AdminExecutor()
    server = AdminPipeServer(secret, pipe_name, executor, sid="S-1-5-18")
    client = AdminPipeClient(secret, pipe_name, io_timeout_s=10.0)

    serve_task = asyncio.create_task(server.serve_forever(), name="serve")
    try:
        # Small sleep so the accept loop can issue its first
        # CreateNamedPipe.
        await asyncio.sleep(0.3)
        op = ReadRegistryOp(
            hive="HKCU", key_path="Environment", value_name="PATH"
        )
        resp = await client.send(op)
        # On success=True the result dict must contain "value" and "type";
        # on False it's either "registry_key_not_found" or
        # "registry_read_failed". Both are valid — we only check that
        # the round-trip infrastructure runs cleanly.
        assert resp.op_id == op.op_id
        if resp.success:
            assert "value" in resp.result
            assert resp.result.get("hive") == "HKCU"
        else:
            assert resp.error_code in (
                "registry_key_not_found", "registry_read_failed",
            )
    finally:
        server.stop()
        try:
            await asyncio.wait_for(serve_task, timeout=5.0)
        except asyncio.TimeoutError:
            serve_task.cancel()
