"""Named-Pipe-Fallback. Plan §5 AD-5 + §10.

Skeleton: Connect-Helper + send/recv-Roundtrip. Vollstaendige Auto-
Failover-Logik (WS down + Pipe up) ist Phase 9.7-Thema. Hier nur die
Bausteine, damit die Tests den Pipe-Pfad simulieren koennen.

Fuer Non-Windows-Umgebungen: ``CAPABLE`` ist ``False``, ``connect`` und
``send_recv`` raisen ``RuntimeError``. Tests skippen entsprechend.
"""

from __future__ import annotations

import logging
import sys
from contextlib import contextmanager
from typing import Any, Iterator

logger = logging.getLogger(__name__)

DEFAULT_PIPE_NAME = r"\\.\pipe\jarvis-overlay"

# Pipe-Buffer-Defaults — unkritisch, beide Richtungen je 64 KiB.
_BUF = 64 * 1024


def _is_windows() -> bool:
    return sys.platform == "win32"


def _import_win32() -> tuple[Any, Any]:
    """Lazy-Import. Gibt (win32file, win32pipe) zurueck."""
    if not _is_windows():
        raise RuntimeError("Named-Pipe ist Windows-only")
    try:
        import win32file  # type: ignore[import-not-found]
        import win32pipe  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "pywin32 nicht installiert — `pip install pywin32`"
        ) from exc
    return win32file, win32pipe


CAPABLE: bool = _is_windows()


@contextmanager
def open_client(pipe_name: str = DEFAULT_PIPE_NAME, *, timeout_ms: int = 5000) -> Iterator[Any]:
    """Verbindet als Client zur existierenden Pipe.

    Yieldet das ``HANDLE``. Schliesst es sauber im Exit-Branch. Raised
    ``RuntimeError`` falls die Pipe nicht existiert oder wir auf Non-
    Windows laufen.
    """
    win32file, win32pipe = _import_win32()
    import pywintypes  # type: ignore[import-not-found]
    import time as _time

    # ``WaitNamedPipe`` ist auf Win11 unreliable (returnt False obwohl der
    # Server in ``ConnectNamedPipe`` blockiert). Robusterer Pfad: direkt
    # ``CreateFile`` aufrufen und bei ``ERROR_PIPE_BUSY``/``FILE_NOT_FOUND``
    # in kurzen Abstaenden retry-en.
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
                    f"Named-Pipe Connect-Fehler: {pipe_name} ({exc})"
                ) from exc
            _time.sleep(0.025)
    assert handle is not None, "unreachable: handle must be set after loop"
    del last_err  # unused outside loop

    try:
        # PIPE_READMODE_MESSAGE: jede WriteFile entspricht einer ReadFile.
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
    """Reine ``CreateNamedPipe``. Returnt das Handle ohne zu blockieren.

    ``accept_server_connection(handle)`` blockiert dann separat bis ein
    Client verbindet. Splittet das Lifecycle damit Tests den Race vor
    ``WaitNamedPipe`` nicht durch Sleep loesen muessen.
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
    """Blockiert auf ``ConnectNamedPipe(handle)``."""
    _, win32pipe = _import_win32()
    win32pipe.ConnectNamedPipe(handle, None)


def close_server_pipe(handle: Any) -> None:
    """Disconnect + Close-Handle. Idempotent."""
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
    """Server-seitige Pipe. Erzeugt sie und blockiert auf ``ConnectNamedPipe``.

    Wird in Phase 9.7 durch eine Multi-Connection-Variante ersetzt; hier
    nur das Single-Connection-Skelett, damit Tests den Roundtrip pruefen
    koennen. Fuer feinere Test-Kontrolle siehe ``create_server_pipe`` +
    ``accept_server_connection``.
    """
    handle = create_server_pipe(pipe_name)
    try:
        accept_server_connection(handle)
        yield handle
    finally:
        close_server_pipe(handle)


def send(handle: Any, payload: bytes) -> int:
    """Schreibt eine Message. Returnt geschriebene Bytes."""
    win32file, _ = _import_win32()
    err, written = win32file.WriteFile(handle, payload)
    if err != 0:
        raise OSError(f"WriteFile error={err}")
    return written


def recv(handle: Any, max_bytes: int = _BUF) -> bytes:
    """Liest eine Message. Returnt rohe Bytes (UTF-8 JSON erwartet)."""
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
