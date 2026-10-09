"""Opt-in real Docker proof; no model calls or credentials."""

from __future__ import annotations

import asyncio
import base64
import json
import os

import pytest

from jarvis.society.sandbox import Sandbox, _docker, status

pytestmark = pytest.mark.skipif(
    os.environ.get("JARVIS_SANDBOX_LIVE") != "1",
    reason="requires explicitly selected local Docker",
)


async def test_write_test_export_isolation_timeout_and_cancel(tmp_path, monkeypatch):
    readiness = await status()
    assert readiness["available"], readiness
    sandbox = Sandbox(tmp_path / "isolated-agent")
    monkeypatch.setenv("JARVIS_TEST_SECRET", "must-not-enter-container")
    try:
        code = (
            "import os, socket, unittest\nfrom pathlib import Path\n"
            "class Checks(unittest.TestCase):\n"
            " def test_isolation(self):\n"
            "  self.assertEqual(os.getuid(),65532)\n"
            "  self.assertNotIn('JARVIS_TEST_SECRET',os.environ)\n"
            "  self.assertFalse(Path('/var/run/docker.sock').exists())\n"
            "  self.assertFalse(Path('/host').exists())\n"
            "  with self.assertRaises(OSError): Path('/etc/host-write').write_text('no')\n"
            "  with self.assertRaises(OSError): socket.create_connection(('1.1.1.1',443),.2)\n"
            "if __name__=='__main__': unittest.main()\n"
        )
        rc, out = await sandbox.file("write", "test_generated.py", code)
        assert rc == 0, out
        rc, out = await sandbox.execute("python -m unittest -v test_generated.py")
        assert rc == 0 and b"OK" in out, out
        rc, out = await sandbox.file("download", "test_generated.py")
        assert rc == 0 and base64.b64decode(out) == code.encode()
        rc, out = await sandbox.file("read", "../../etc/passwd")
        assert rc != 0 and b"Path must stay" in out
        rc, out = await sandbox.file("list")
        assert rc == 0 and any(f["name"] == "test_generated.py" for f in json.loads(out))
        with pytest.raises(TimeoutError):
            await sandbox.execute("sleep 30", timeout_s=1)
        task = asyncio.create_task(sandbox.execute("sleep 30"))
        await asyncio.sleep(3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        rc, out = await _docker("ps", "-aq", "--filter", f"volume={sandbox.volume}")
        assert rc == 0 and not out.strip(), "sandbox left a running or stopped container"
    finally:
        # Exact test-owned volume only. Never sweep other agents' persistent data.
        rc, out = await _docker("volume", "rm", sandbox.volume)
        assert rc == 0, out
