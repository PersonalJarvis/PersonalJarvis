"""IPC-Loopback-Test: AdminPipeServer + AdminPipeClient im gleichen Prozess.

Kein echter UAC-Prompt — wir starten den Server im pytest-Event-Loop und
schicken eine harmlose ``read_registry``-Op gegen ``HKCU\\Environment``.

Der Test ist ``@pytest.mark.phase5 + @pytest.mark.skip_ci``, weil er
Windows-Named-Pipes braucht (pywin32). Lokal auf dem Dev-Rechner laeuft er.
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
        pytest.skip("pywin32 nicht verfuegbar")

    secret = b"X" * 32
    pipe_name = rf"\\.\pipe\jarvis-admin-test-{uuid.uuid4().hex}"
    executor = AdminExecutor()
    server = AdminPipeServer(secret, pipe_name, executor, sid="S-1-5-18")
    client = AdminPipeClient(secret, pipe_name, io_timeout_s=10.0)

    serve_task = asyncio.create_task(server.serve_forever(), name="serve")
    try:
        # Kleiner sleep, damit der Accept-Loop seine erste CreateNamedPipe
        # absetzen kann.
        await asyncio.sleep(0.3)
        op = ReadRegistryOp(
            hive="HKCU", key_path="Environment", value_name="PATH"
        )
        resp = await client.send(op)
        # Bei success=True muss der Result-Dict "value" und "type" enthalten;
        # bei False ist entweder "registry_key_not_found" oder
        # "registry_read_failed". Beides ist valide — wir pruefen nur, dass
        # die Round-Trip-Infrastruktur sauber laeuft.
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
