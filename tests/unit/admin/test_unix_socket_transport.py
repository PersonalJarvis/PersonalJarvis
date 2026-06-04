"""UnixSocketTransport security gates (Wave 3, sub-task 3.2).

Two layers, defense in depth (the SDDL-owner-ACE equivalent + HMAC):

1. Filesystem ACL — the socket file is created ``0600`` inside a ``0700`` dir.
2. Peer-credential check — a peer whose uid != the server uid is rejected.

The peer-cred logic is OS-agnostic (it reads via a small ``read_peer_uid`` shim
that tests monkeypatch), so the rejection/acceptance gate runs on every OS,
including the Windows dev box. The real AF_UNIX bind needs POSIX and is covered
by ``tests/integration/test_admin_unix_loopback.py`` (skipped on Windows).
"""
from __future__ import annotations

import os
import stat
import sys

import pytest

from jarvis.admin import unix_socket
from jarvis.admin.transport import AdminTransport
from jarvis.admin.unix_socket import UnixSocketTransport


class _FakeWriter:
    """Minimal asyncio.StreamWriter stand-in exposing ``get_extra_info``."""

    def __init__(self, sock: object) -> None:
        self._sock = sock

    def get_extra_info(self, name: str) -> object:
        return self._sock if name == "socket" else None


def test_unix_socket_transport_satisfies_protocol():
    assert isinstance(UnixSocketTransport("/tmp/x.sock"), AdminTransport)


def test_peer_uid_match_accepted(monkeypatch):
    t = UnixSocketTransport("/tmp/x.sock")
    monkeypatch.setattr(t, "_server_uid", 1000)
    monkeypatch.setattr(unix_socket, "read_peer_uid", lambda _sock: 1000)
    assert t._peer_uid_ok(_FakeWriter(object())) is True


def test_peer_uid_mismatch_rejected(monkeypatch):
    """A peer running as a different uid is rejected (the SDDL-ACE equivalent)."""
    t = UnixSocketTransport("/tmp/x.sock")
    monkeypatch.setattr(t, "_server_uid", 1000)
    monkeypatch.setattr(unix_socket, "read_peer_uid", lambda _sock: 31337)
    assert t._peer_uid_ok(_FakeWriter(object())) is False


def test_peer_uid_unverifiable_fails_closed(monkeypatch):
    """No peer-credential primitive -> cannot verify -> reject (fail closed)."""
    t = UnixSocketTransport("/tmp/x.sock")
    monkeypatch.setattr(unix_socket, "read_peer_uid", lambda _sock: None)
    assert t._peer_uid_ok(_FakeWriter(object())) is False


def test_peer_uid_no_socket_rejected():
    t = UnixSocketTransport("/tmp/x.sock")
    assert t._peer_uid_ok(_FakeWriter(None)) is False


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="AF_UNIX bind + os.getuid are POSIX-only; logic is covered by the "
           "monkeypatched peer-cred tests above and the unix loopback test.",
)
@pytest.mark.asyncio
async def test_socket_file_is_0600_in_0700_dir(tmp_path):
    """The bound socket lands 0600 inside a 0700 directory (the FS ACL)."""
    import asyncio

    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(mode=0o700)
    sock_path = str(runtime_dir / "jarvis-admin.sock")
    transport = UnixSocketTransport(sock_path)

    async def _handler(raw: bytes) -> bytes:
        return raw

    serve_task = asyncio.create_task(transport.serve(_handler))
    try:
        # Wait until the socket file appears.
        for _ in range(100):
            if os.path.exists(sock_path):
                break
            await asyncio.sleep(0.01)
        assert os.path.exists(sock_path), "socket file was never bound"
        mode = stat.S_IMODE(os.stat(sock_path).st_mode)
        assert mode == 0o600, f"socket mode {oct(mode)} != 0600"
        dir_mode = stat.S_IMODE(os.stat(runtime_dir).st_mode)
        assert dir_mode == 0o700, f"dir mode {oct(dir_mode)} != 0700"
    finally:
        transport.stop()
        await asyncio.wait_for(serve_task, timeout=2.0)


def test_read_peer_uid_documents_macos_branch():
    """The macOS LOCAL_PEERCRED / getpeereid branch is structurally reachable."""
    import socket as _socket

    # On Linux SO_PEERCRED exists; on macOS getpeereid/LOCAL_PEERCRED is used.
    # We only assert the shim is callable and returns int|None for a real socket.
    s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    try:
        result = unix_socket.read_peer_uid(s)
        assert result is None or isinstance(result, int)
    finally:
        s.close()
