"""Named-pipe fallback. Plan §5 AD-5 + §10.

Skeleton: connect helper + send/recv roundtrip. The full auto-failover
logic (WS down + pipe up) is a Phase 9.7 topic. This only has the
building blocks, so tests can simulate the pipe path.

For non-Windows environments: ``CAPABLE`` is ``False``, ``connect``
and ``send_recv`` raise ``RuntimeError``. Tests skip accordingly.
"""

from __future__ import annotations

import logging
import sys
from contextlib import contextmanager
from typing import Any, Iterator

logger = logging.getLogger(__name__)

DEFAULT_PIPE_NAME = r"\\.\pipe\jarvis-overlay"

# Pipe buffer defaults — uncritical, 64 KiB each direction.
_BUF = 64 * 1024


def _is_windows() -> bool:
    return sys.platform == "win32"


def _import_win32() -> tuple[Any, Any]:
    """Lazy import. Returns (win32file, win32pipe)."""
    if not _is_windows():
        raise RuntimeError("Named pipes are Windows-only")
    try:
        import win32file  # type: ignore[import-not-found]
        import win32pipe  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "pywin32 not installed — `pip install pywin32`"
        ) from exc
    return win32file, win32pipe


CAPABLE: bool = _is_windows()


@contextmanager
def open_client(pipe_name: str = DEFAULT_PIPE_NAME, *, timeout_ms: int = 5000) -> Iterator[Any]:
    """Connects as a client to the existing pipe.

    Yields the ``HANDLE``. Closes it cleanly in the exit branch. Raises
    ``RuntimeError`` if the pipe doesn't exist or we're running on
    non-Windows.
    """
    win32file, win32pipe = _import_win32()
    import pywintypes  # type: ignore[import-not-found]
    import time as _time

    # ``WaitNamedPipe`` is unreliable on Win11 (returns False even
    # though the server is blocked in ``ConnectNamedPipe``). A more
    # robust path: call ``CreateFile`` directly and retry at short
    # intervals on ``ERROR_PIPE_BUSY``/``FILE_NOT_FOUND``.
    deadline = _time.monotonic() + (timeout_ms / 1000.0)
    last_err: Exception | None = None
    handle = None
    while True:
        try:
            handle = win32file.CreateFile(
                pipe_name,
                win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                0,
                None,
                win32file.OPEN_EXISTING,
                0,
                None,
            )
            break
        except (OSError, pywintypes.error) as exc:
            last_err = exc
            if _time.monotonic() >= deadline:
                raise RuntimeError(
                    f"Named-pipe connect error: {pipe_name} ({exc})"
                ) from exc
            _time.sleep(0.025)
    assert handle is not None, "unreachable: handle must be set after loop"
    del last_err  # unused outside loop

    try:
        # PIPE_READMODE_MESSAGE: every WriteFile corresponds to one ReadFile.
        win32pipe.SetNamedPipeHandleState(
            handle, win32pipe.PIPE_READMODE_MESSAGE, None, None
        )
        yield handle
    finally:
        try:
            win32file.CloseHandle(handle)
        except Exception:  # noqa: BLE001
            pass


def create_server_pipe(pipe_name: str = DEFAULT_PIPE_NAME) -> Any:
    """Pure ``CreateNamedPipe``. Returns the handle without blocking.

    ``accept_server_connection(handle)`` then blocks separately until a
    client connects. Splits the lifecycle so tests don't have to
    resolve the race before ``WaitNamedPipe`` via sleep.
    """
    win32file, win32pipe = _import_win32()
    return win32pipe.CreateNamedPipe(
        pipe_name,
        win32pipe.PIPE_ACCESS_DUPLEX,
        win32pipe.PIPE_TYPE_MESSAGE
        | win32pipe.PIPE_READMODE_MESSAGE
        | win32pipe.PIPE_WAIT,
        1,  # max instances
        _BUF,
        _BUF,
        0,
        None,
    )


def accept_server_connection(handle: Any) -> None:
    """Blocks on ``ConnectNamedPipe(handle)``."""
    _, win32pipe = _import_win32()
    win32pipe.ConnectNamedPipe(handle, None)


def close_server_pipe(handle: Any) -> None:
    """Disconnect + close handle. Idempotent."""
    win32file, win32pipe = _import_win32()
    try:
        win32pipe.DisconnectNamedPipe(handle)
    except Exception:  # noqa: BLE001
        pass
    try:
        win32file.CloseHandle(handle)
    except Exception:  # noqa: BLE001
        pass


@contextmanager
def open_server(pipe_name: str = DEFAULT_PIPE_NAME) -> Iterator[Any]:
    """Server-side pipe. Creates it and blocks on ``ConnectNamedPipe``.

    Gets replaced by a multi-connection variant in Phase 9.7; this is
    only the single-connection skeleton, so tests can verify the
    roundtrip. For finer test control see ``create_server_pipe`` +
    ``accept_server_connection``.
    """
    handle = create_server_pipe(pipe_name)
    try:
        accept_server_connection(handle)
        yield handle
    finally:
        close_server_pipe(handle)


def send(handle: Any, payload: bytes) -> int:
    """Writes a message. Returns the number of bytes written."""
    win32file, _ = _import_win32()
    err, written = win32file.WriteFile(handle, payload)
    if err != 0:
        raise OSError(f"WriteFile error={err}")
    return written


def recv(handle: Any, max_bytes: int = _BUF) -> bytes:
    """Reads a message. Returns the raw bytes (UTF-8 JSON expected)."""
    win32file, _ = _import_win32()
    err, data = win32file.ReadFile(handle, max_bytes)
    if err != 0:
        raise OSError(f"ReadFile error={err}")
    if isinstance(data, memoryview):
        return bytes(data)
    return bytes(data)


__all__ = [
    "CAPABLE",
    "DEFAULT_PIPE_NAME",
    "accept_server_connection",
    "close_server_pipe",
    "create_server_pipe",
    "open_client",
    "open_server",
    "recv",
    "send",
]
