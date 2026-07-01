"""Named-Pipe-Roundtrip — Windows-only, sonst skip."""

from __future__ import annotations

import sys
import threading
import time

import pytest

from overlay import ipc_pipe


pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("win"),
    reason="Named-Pipe-Fallback ist Windows-only (pywin32)",
)


def test_capable_flag_matches_platform() -> None:
    assert ipc_pipe.CAPABLE is sys.platform.startswith("win")


def test_default_pipe_name() -> None:
    assert ipc_pipe.DEFAULT_PIPE_NAME == r"\\.\pipe\jarvis-overlay"


def test_send_recv_roundtrip() -> None:
    """Server in a thread, client in the main thread: sends JSON, receives it back.

    The pipe is created in the main thread (CreateNamedPipe), only then does
    the server thread start on ConnectNamedPipe. That way there's no race before
    WaitNamedPipe.
    """
    pytest.importorskip("win32pipe")
    pytest.importorskip("win32file")

    pipe = r"\\.\pipe\jarvis-overlay-test-" + str(time.time_ns())
    handle = ipc_pipe.create_server_pipe(pipe)

    server_payload: dict[str, bytes] = {}
    server_err: list[BaseException] = []

    def server_thread() -> None:
        try:
            ipc_pipe.accept_server_connection(handle)
            data = ipc_pipe.recv(handle)
            ipc_pipe.send(handle, b"echo:" + data)
            server_payload["data"] = data
        except BaseException as exc:  # noqa: BLE001
            server_err.append(exc)

    t = threading.Thread(target=server_thread, daemon=True)
    t.start()

    try:
        with ipc_pipe.open_client(pipe, timeout_ms=2000) as h:
            sent = b'{"type":"state","payload":{"state":"idle"}}'
            ipc_pipe.send(h, sent)
            reply = ipc_pipe.recv(h)
            assert reply == b"echo:" + sent
        t.join(timeout=2.0)
    finally:
        ipc_pipe.close_server_pipe(handle)

    assert not server_err, server_err
    assert server_payload.get("data", b"") != b""


def test_open_client_raises_when_pipe_missing() -> None:
    pytest.importorskip("win32pipe")
    bogus = r"\\.\pipe\definitely-not-running-" + str(time.time_ns())
    with pytest.raises(RuntimeError):
        with ipc_pipe.open_client(bogus, timeout_ms=200):
            pass
