"""Admin IPC transport seam (Wave 3, sub-task 3.1; AD-12 + AD-7 + AD-6).

The privileged-helper IPC used to hard-wire the Windows Named-Pipe primitives
(``CreateNamedPipe``/``ConnectNamedPipe``/``ReadFile``/``WriteFile`` + the
SDDL-ACL) directly inside ``jarvis/admin/ipc.py``. This module introduces the
uniform AD-6 seam so the *transport* (the byte pipe between the parent app and
the elevated helper) can swap per OS while the **security core stays untouched**
(AD-12): the HMAC/envelope/nonce/timestamp layer in ``ipc.py``
(``_canonical_args_json``/``_compute_hmac``/``_decode_request``/
``_encode_response``/the nonce LRU) is transport-agnostic and reused verbatim by
every transport.

What lives here:

* :class:`AdminTransport` — the ``Protocol`` both sides implement.
    - server: ``async def serve(handler)`` where
      ``handler: Callable[[bytes], Awaitable[bytes]]`` receives a raw request
      envelope and returns a raw response — exactly the bytes-level seam
      ``_decode_request`` / ``_encode_response`` already operate on.
    - client: ``async def roundtrip(raw: bytes) -> bytes``.
* :class:`NamedPipeTransport` — the Windows implementation. It wraps the
  **relocated** pipe code from ``ipc.py`` with **no behavior change** (AD-7):
  the SDDL-ACL ``D:(A;;FA;;;<SID>)``, MESSAGE-mode reads, the per-connection
  task structure, and the ``WaitNamedPipe``-then-``CreateFile`` client roundtrip
  are all preserved exactly.
* :func:`make_admin_transport` — the ``detect_platform()`` factory:
  ``win32`` -> :class:`NamedPipeTransport`; anything else ->
  :class:`UnixSocketTransport` (sub-task 3.2).

Import-cleanliness contract (HN-7): ``win32pipe``/``win32file``/
``win32security``/``pywintypes`` are imported **lazily inside method bodies**,
never at module scope. ``import jarvis.admin.transport`` therefore succeeds on a
Linux/macOS VPS that has no ``pywin32`` (the named-pipe path simply is never
selected by the factory there). The Windows SID/pipe-name helpers
(:func:`current_user_sid`, :func:`default_pipe_name`, :func:`_build_sddl`) were
relocated here from ``ipc.py`` for the same reason.
"""

from __future__ import annotations

import asyncio
import os
import threading
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from loguru import logger

from jarvis.platform import detect_platform

if TYPE_CHECKING:
    from .transport import AdminTransport as _AdminTransport  # noqa: F401

# Bytes-level request handler: raw envelope in, raw response out. This is the
# exact seam the reused HMAC core (``_decode_request`` -> executor ->
# ``_encode_response``) already speaks.
RequestHandler = Callable[[bytes], Awaitable[bytes]]


# ---------------------------------------------------------------------
# Constants (transport-specific; the HMAC/payload constants stay in ipc.py)
# ---------------------------------------------------------------------

# Max-Payload (12 MB; 10 MB content_b64 in write_protected_path + envelope). Kept
# byte-identical to ipc.py:_MAX_PAYLOAD_BYTES so the transport caps match the core.
_MAX_PAYLOAD_BYTES = 12 * 1024 * 1024
# Pipe buffer (server side), relocated verbatim from ipc.py.
_PIPE_IN_BUFFER = _MAX_PAYLOAD_BYTES
_PIPE_OUT_BUFFER = 64 * 1024


class _NamedPipeStopped(Exception):
    """Internal control flow for I/O cancelled by ``stop()``."""


# ---------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------


@runtime_checkable
class AdminTransport(Protocol):
    """The byte transport between the parent app and the elevated helper.

    A single object can serve both roles in tests (server + client over the same
    address), exactly like the named-pipe loopback does. Both methods are async
    and never expose the underlying OS primitive to the caller — the security
    core only ever sees ``bytes``.
    """

    async def serve(self, handler: RequestHandler) -> None:
        """Accept connections forever; for each, ``await handler(raw)`` and reply.

        ``handler`` receives the raw request envelope and returns the raw
        response bytes. Runs until :meth:`stop` is called.
        """
        ...

    async def roundtrip(self, raw: bytes) -> bytes:
        """Client side: connect, write ``raw``, read and return the response."""
        ...

    def stop(self) -> None:
        """Signal the server accept-loop to exit at the next opportunity."""
        ...


# ---------------------------------------------------------------------
# Windows SID / pipe-name helpers (relocated from ipc.py — AD-7, verbatim)
# ---------------------------------------------------------------------


def current_user_sid() -> str:
    """Returns the SID of the current process user as a string (``S-1-5-21-...``).

    Falls back to a deterministic placeholder when pywin32 is not available
    (e.g. on CI without Windows). Tests must not rely on this — they should
    patch this call instead.
    """
    if os.name != "nt":
        return "S-0-0"
    try:
        import win32api  # type: ignore[import-not-found,import-untyped]  # noqa: PLC0415
        import win32con  # type: ignore[import-not-found,import-untyped]  # noqa: PLC0415
        import win32security  # type: ignore[import-not-found,import-untyped]  # noqa: PLC0415

        token = win32security.OpenProcessToken(
            win32api.GetCurrentProcess(), win32con.TOKEN_QUERY
        )
        sid, _attrs = win32security.GetTokenInformation(
            token, win32security.TokenUser
        )
        return win32security.ConvertSidToStringSid(sid)
    except Exception:  # noqa: BLE001
        # Fallback for non-Windows / CI. The real helper aborts before
        # reaching this point (see helper.py).
        return "S-0-0"


def default_pipe_name() -> str:
    """Returns the pipe name for the current user."""
    sid = current_user_sid()
    return rf"\\.\pipe\jarvis-admin-{sid}"


def _build_sddl(sid: str) -> str:
    """SDDL string: full access for the specified SID only.

    ADR-0001 §SDDL: ``D:(A;;FA;;;<SID>)``. No inheritance, no
    Everyone/Authenticated-Users ACE.
    """
    return f"D:(A;;FA;;;{sid})"


# ---------------------------------------------------------------------
# NamedPipeTransport (Windows) — relocated pipe code, behavior-identical
# ---------------------------------------------------------------------


class NamedPipeTransport:
    """``AdminTransport`` over a Windows Named Pipe (AD-7, no behavior change).

    Server side mirrors the relocated ``AdminPipeServer`` accept loop
    (``_accept_one`` -> ``CreateNamedPipe`` with the SDDL-ACL -> one task per
    connection). Client side mirrors the relocated ``AdminPipeClient._roundtrip``
    (``WaitNamedPipe`` -> ``CreateFile`` -> MESSAGE-mode write/read).

    The same object can be used for both roles in a loopback test; the
    ``handler`` passed to :meth:`serve` is the reused
    ``_decode_request`` -> executor -> ``_encode_response`` chain.
    """

    def __init__(
        self,
        pipe_name: str | None = None,
        *,
        sid: str | None = None,
        connect_timeout_ms: int = 5000,
    ) -> None:
        self._pipe_name = pipe_name or default_pipe_name()
        self._sid = sid or current_user_sid()
        self._connect_timeout_ms = connect_timeout_ms
        self._stop = threading.Event()
        self._tasks: set[asyncio.Task[None]] = set()
        self._stop_handle: Any | None = None
        self._stop_handle_lock = threading.Lock()
        self._accept_ready = threading.Event()

    @property
    def address(self) -> str:
        """The pipe path; used by the elevator to bind the helper (3.4)."""
        return self._pipe_name

    # ------------------------------------------------------------------
    # Server — accept loop (relocated from AdminPipeServer.serve_forever)
    # ------------------------------------------------------------------

    async def serve(self, handler: RequestHandler) -> None:
        """Runs until ``stop()`` is called. Each connection gets its own task."""
        logger.info("admin_pipe_transport.start",
                    pipe=self._pipe_name, sid=self._sid)
        try:
            while not self._stop.is_set():
                accept_task = asyncio.create_task(
                    asyncio.to_thread(self._accept_one),
                    name="admin_pipe_accept",
                )
                try:
                    # Native I/O outlives a cancelled asyncio wrapper. Shielding
                    # lets cancellation signal stop and then reap the worker.
                    handle = await asyncio.shield(accept_task)
                except asyncio.CancelledError:
                    self.stop()
                    try:
                        await accept_task
                    except Exception:  # noqa: BLE001
                        logger.exception("admin_pipe_transport.accept_cancel_error")
                    raise
                except Exception:  # noqa: BLE001
                    logger.exception("admin_pipe_transport.accept_error")
                    if self._stop.is_set():
                        break
                    await asyncio.sleep(0.1)
                    continue
                if handle is None:
                    continue
                if self._stop.is_set():
                    await asyncio.to_thread(self._safe_close, handle)
                    break
                task = asyncio.create_task(
                    self._handle_connection(handle, handler),
                    name="admin_pipe_conn",
                )
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
        finally:
            try:
                if self._tasks:
                    await asyncio.gather(*self._tasks, return_exceptions=True)
            finally:
                self._close_stop_handle()
                logger.info("admin_pipe_transport.stopped")

    def _get_stop_handle(self) -> Any | None:
        """Return the manual-reset Win32 event shared by pending pipe I/O."""
        if os.name != "nt":
            return None
        import win32event  # type: ignore[import-not-found,import-untyped]  # noqa: PLC0415

        with self._stop_handle_lock:
            if self._stop_handle is None:
                self._stop_handle = win32event.CreateEvent(
                    None,
                    True,
                    self._stop.is_set(),
                    None,
                )
            return self._stop_handle

    def _close_stop_handle(self) -> None:
        """Close the native stop event after all overlapped I/O has drained."""
        if os.name != "nt":
            return
        import win32file  # type: ignore[import-not-found,import-untyped]  # noqa: PLC0415

        with self._stop_handle_lock:
            handle = self._stop_handle
            self._stop_handle = None
            if handle is not None:
                win32file.CloseHandle(handle)

    def _accept_one(self) -> Any:
        """Wait for one client or the stop event, without stranding a thread."""
        if os.name != "nt":
            return None
        import _winapi  # noqa: PLC0415

        import win32file  # type: ignore[import-not-found,import-untyped]  # noqa: PLC0415
        import win32pipe  # type: ignore[import-not-found,import-untyped]  # noqa: PLC0415
        import win32security  # type: ignore[import-not-found,import-untyped]  # noqa: PLC0415

        stop_handle = self._get_stop_handle()
        if stop_handle is None or self._stop.is_set():
            return None

        # Security descriptor with SDDL ACL.
        sd = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
            _build_sddl(self._sid), win32security.SDDL_REVISION_1
        )
        sa = win32security.SECURITY_ATTRIBUTES()
        sa.SECURITY_DESCRIPTOR = sd
        sa.bInheritHandle = False

        handle = win32pipe.CreateNamedPipe(
            self._pipe_name,
            win32pipe.PIPE_ACCESS_DUPLEX | win32file.FILE_FLAG_OVERLAPPED,
            win32pipe.PIPE_TYPE_MESSAGE
            | win32pipe.PIPE_READMODE_MESSAGE
            | win32pipe.PIPE_WAIT,
            1,                              # max instances: one client is enough
            _PIPE_OUT_BUFFER, _PIPE_IN_BUFFER,
            0, sa,
        )
        accepted = False
        overlapped = None
        try:
            try:
                overlapped = _winapi.ConnectNamedPipe(
                    int(handle),
                    overlapped=True,
                )
            except OSError as exc:
                # A client can connect and leave between pipe creation and the
                # accept call; Windows still considers that instance accepted.
                if exc.winerror != _winapi.ERROR_NO_DATA:
                    raise
                if self._stop.is_set():
                    return None
                accepted = True
                return handle

            self._accept_ready.set()
            wait_result = _winapi.WaitForMultipleObjects(
                [overlapped.event, int(stop_handle)],
                False,
                _winapi.INFINITE,
            )
            if wait_result == 1:
                overlapped.cancel()
                _transferred, error = overlapped.GetOverlappedResult(True)
                if error not in (0, _winapi.ERROR_OPERATION_ABORTED):
                    raise OSError(error, "named-pipe accept cancellation failed")
                return None
            if wait_result != 0:
                overlapped.cancel()
                overlapped.GetOverlappedResult(True)
                raise OSError(wait_result, "named-pipe accept wait failed")

            _transferred, error = overlapped.GetOverlappedResult(True)
            if error:
                raise OSError(error, "named-pipe accept failed")
            if self._stop.is_set():
                return None
            accepted = True
            return handle
        finally:
            self._accept_ready.clear()
            if not accepted:
                win32file.CloseHandle(handle)

    async def _handle_connection(self, handle: Any, handler: RequestHandler) -> None:
        """Reads exactly one request, dispatches it via ``handler``, sends the response."""
        try:
            raw = await asyncio.to_thread(self._read_message, handle)
        except _NamedPipeStopped:
            await asyncio.to_thread(self._safe_close, handle)
            return
        except OSError as exc:
            logger.warning("admin_pipe_transport.read_error", error=str(exc))
            await asyncio.to_thread(self._safe_close, handle)
            return

        try:
            payload = await handler(raw)
        except Exception:  # noqa: BLE001
            logger.exception("admin_pipe_transport.handler_crashed")
            await asyncio.to_thread(self._safe_close, handle)
            return

        try:
            await asyncio.to_thread(self._write_message, handle, payload)
        except Exception:  # noqa: BLE001
            logger.exception("admin_pipe_transport.write_error")
        finally:
            await asyncio.to_thread(self._safe_close, handle)

    def _wait_for_io(self, overlapped: Any, initial_error: int) -> tuple[int, int]:
        """Wait for overlapped I/O while allowing ``stop()`` to cancel it."""
        if os.name != "nt":
            return (0, 0)
        import _winapi  # noqa: PLC0415

        stop_handle = self._get_stop_handle()
        if stop_handle is None:
            raise _NamedPipeStopped
        if initial_error == _winapi.ERROR_IO_PENDING:
            wait_result = _winapi.WaitForMultipleObjects(
                [overlapped.event, int(stop_handle)],
                False,
                _winapi.INFINITE,
            )
            if wait_result == 1:
                overlapped.cancel()
                _transferred, error = overlapped.GetOverlappedResult(True)
                if error not in (0, _winapi.ERROR_OPERATION_ABORTED):
                    raise OSError(error, "named-pipe I/O cancellation failed")
                raise _NamedPipeStopped
            if wait_result != 0:
                overlapped.cancel()
                overlapped.GetOverlappedResult(True)
                raise OSError(wait_result, "named-pipe I/O wait failed")
        return overlapped.GetOverlappedResult(True)

    def _read_message(self, handle: Any) -> bytes:
        if os.name != "nt":
            return b""
        import _winapi  # noqa: PLC0415

        chunks: list[bytes] = []
        total = 0
        while True:
            overlapped, initial_error = _winapi.ReadFile(
                int(handle),
                65536,
                overlapped=True,
            )
            transferred, error = self._wait_for_io(overlapped, initial_error)
            buffer = overlapped.getbuffer()
            if buffer is None:
                raise OSError("named-pipe read completed without a buffer")
            data = bytes(buffer)
            if transferred != len(data):
                data = data[:transferred]
            chunks.append(data)
            total += transferred
            if total > _MAX_PAYLOAD_BYTES:
                raise OSError("payload exceeds limit")
            if error == _winapi.ERROR_MORE_DATA:
                continue
            if error:
                raise OSError(error, "named-pipe read failed")
            # MESSAGE mode reports success exactly at the message boundary,
            # including a message whose size is an exact multiple of 64 KiB.
            break
        return b"".join(chunks)

    def _write_message(self, handle: Any, data: bytes) -> None:
        if os.name != "nt":
            return
        import _winapi  # noqa: PLC0415

        import win32file  # type: ignore[import-not-found,import-untyped]  # noqa: PLC0415

        overlapped, initial_error = _winapi.WriteFile(
            int(handle),
            data,
            overlapped=True,
        )
        transferred, error = self._wait_for_io(overlapped, initial_error)
        if error:
            raise OSError(error, "named-pipe write failed")
        if transferred != len(data):
            raise OSError("named-pipe write was incomplete")
        win32file.FlushFileBuffers(handle)

    @staticmethod
    def _safe_close(handle: Any) -> None:
        if os.name != "nt":
            return
        try:
            import win32file  # type: ignore[import-not-found,import-untyped]  # noqa: PLC0415
            import win32pipe  # type: ignore[import-not-found,import-untyped]  # noqa: PLC0415

            try:
                win32pipe.DisconnectNamedPipe(handle)
            except Exception:  # noqa: BLE001, S110
                pass
            win32file.CloseHandle(handle)
        except Exception:  # noqa: BLE001, S110
            pass

    def stop(self) -> None:
        """Signal pending native pipe I/O and the accept loop to stop."""
        self._stop.set()
        if os.name != "nt":
            return
        import win32event  # type: ignore[import-not-found,import-untyped]  # noqa: PLC0415

        with self._stop_handle_lock:
            if self._stop_handle is not None:
                win32event.SetEvent(self._stop_handle)

    # ------------------------------------------------------------------
    # Client — roundtrip (relocated from AdminPipeClient._roundtrip)
    # ------------------------------------------------------------------

    async def roundtrip(self, raw: bytes) -> bytes:
        """Async wrapper around the blocking pipe connect+write+read."""
        return await asyncio.to_thread(self._roundtrip, raw)

    def _roundtrip(self, raw: bytes) -> bytes:
        """Blocking pipe connect + write + read."""
        if os.name != "nt":
            return b""
        import pywintypes  # type: ignore[import-not-found,import-untyped]  # noqa: PLC0415
        import win32file  # type: ignore[import-not-found,import-untyped]  # noqa: PLC0415
        import win32pipe  # type: ignore[import-not-found,import-untyped]  # noqa: PLC0415
        try:
            win32pipe.WaitNamedPipe(self._pipe_name, self._connect_timeout_ms)
        except pywintypes.error as exc:
            raise FileNotFoundError(str(exc)) from exc

        handle = win32file.CreateFile(
            self._pipe_name,
            win32file.GENERIC_READ | win32file.GENERIC_WRITE,
            0,                              # no sharing
            None,                           # no SA
            win32file.OPEN_EXISTING,
            0, None,
        )
        try:
            win32pipe.SetNamedPipeHandleState(
                handle, win32pipe.PIPE_READMODE_MESSAGE, None, None
            )
            win32file.WriteFile(handle, raw)
            win32file.FlushFileBuffers(handle)
            chunks: list[bytes] = []
            total = 0
            while True:
                _rc, data = win32file.ReadFile(handle, 65536)
                chunks.append(data)
                total += len(data)
                if len(data) < 65536:
                    break
                if total >= _MAX_PAYLOAD_BYTES:
                    raise OSError("response exceeds limit")
            return b"".join(chunks)
        finally:
            try:
                win32file.CloseHandle(handle)
            except Exception:  # noqa: BLE001, S110
                pass


# ---------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------


def make_admin_transport(
    address: str | None = None,
    *,
    sid: str | None = None,
) -> AdminTransport:
    """Select the admin IPC transport for this host (AD-6 factory).

    * ``win32`` -> :class:`NamedPipeTransport` (the relocated SDDL-ACL pipe).
    * anything else -> :class:`UnixSocketTransport` (0700 socket + peer-cred,
      sub-task 3.2).

    Never raises (AD-6 / the acceptance criterion that this exits 0 on every OS).
    The ``address`` is the pipe name (Windows) or the socket path (Unix); when
    ``None`` the per-OS default is used. ``sid`` is honored only by the Windows
    transport (it scopes the SDDL-ACL).
    """
    if detect_platform() == "win32":
        return NamedPipeTransport(address, sid=sid)
    # Lazy import: unix_socket imports nothing Windows-only, but keeping it lazy
    # mirrors the seam style and avoids an import cost on the Windows path.
    from .unix_socket import UnixSocketTransport

    return UnixSocketTransport(address)


__all__ = [
    "AdminTransport",
    "NamedPipeTransport",
    "RequestHandler",
    "make_admin_transport",
    "current_user_sid",
    "default_pipe_name",
]
