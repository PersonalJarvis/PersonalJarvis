"""Unix domain socket admin transport (Wave 3, sub-task 3.2; AD-12).

The POSIX sibling of :class:`~jarvis.admin.transport.NamedPipeTransport`. Where
Windows scopes the named pipe with an SDDL owner ACE
(``D:(A;;FA;;;<SID>)``), this transport scopes an ``AF_UNIX`` ``SOCK_STREAM``
socket two ways, defense in depth:

1. **Filesystem ACL** — the socket file is created ``0600`` inside a ``0700``
   directory (``$XDG_RUNTIME_DIR/jarvis-admin-<uid>.sock``, falling back to a
   ``0700`` ``tempfile.mkdtemp`` when ``$XDG_RUNTIME_DIR`` is absent). This is
   the direct equivalent of the pipe ACL: only the owning uid can reach it.
   Addresses that exceed Darwin's shorter ``AF_UNIX`` limit are mapped
   deterministically into an owner-only per-uid directory under ``/tmp``.
2. **Peer-credential check** — on every accepted connection the server reads the
   connecting process's uid (Linux: ``SO_PEERCRED``; macOS: ``LOCAL_PEERCRED`` /
   ``getpeereid``) and **rejects any peer whose uid != the server process uid**.
   This is the security equivalent of the Windows SDDL owner ACE, and the HMAC
   envelope check in ``ipc._decode_request`` still runs on top — exactly how the
   Windows transport pairs the pipe ACL with HMAC.

The accept-loop / per-connection-task structure mirrors
``NamedPipeTransport.serve`` so a slow op never blocks accept. On a headless box
with no ``$XDG_RUNTIME_DIR`` the transport still *constructs* (it is just a local
socket); refusal of privileged work happens one layer up at the
``NullElevator`` (sub-task 3.4), never here.

Import-cleanliness contract (HN-7): nothing platform-only is imported at module
scope. ``socket`` / ``os`` / ``struct`` are stdlib and cross-platform; the
``AF_UNIX``-specific calls live inside method bodies and are guarded so that
``import jarvis.admin.unix_socket`` succeeds even on Windows (where the factory
never selects this transport, but tests still import the module).
"""

from __future__ import annotations

import asyncio
import errno
import hashlib
import os
import socket
import stat
import struct
import tempfile
from collections.abc import Awaitable, Callable

from loguru import logger

RequestHandler = Callable[[bytes], Awaitable[bytes]]

# Max-Payload kept byte-identical to ipc._MAX_PAYLOAD_BYTES so the read cap
# matches the security core (12 MB; 10 MB content_b64 + envelope).
_MAX_PAYLOAD_BYTES = 12 * 1024 * 1024

# Permission bits: 0700 dir, 0600 socket file (owner-only). Mirrors the SDDL
# owner-only ACE on Windows.
_DIR_MODE = 0o700
_SOCK_MODE = 0o600

# Darwin's ``sockaddr_un.sun_path`` is shorter than Linux's. Keep the encoded
# path below 100 bytes so the trailing NUL and the platform-specific structure
# layout always fit (macOS allows 104 bytes, Linux 108). A character count is
# insufficient because non-ASCII filesystem paths may occupy multiple bytes.
_MAX_SOCKET_PATH_BYTES = 100


def _private_short_socket_dir(uid: int) -> str:
    """Return a deterministic owner-only directory for shortened addresses."""
    # The fixed short prefix is deliberate: Darwin's AF_UNIX address budget is
    # too small for its ordinary per-user TMPDIR. Ownership, type, and mode are
    # validated below before the directory is trusted.
    candidates = ("/tmp", tempfile.gettempdir())  # noqa: S108
    seen: set[str] = set()
    errors: list[OSError] = []

    for base in candidates:
        if not base or base in seen:
            continue
        seen.add(base)
        directory = os.path.join(base, f"jarvis-admin-{uid}")
        try:
            os.mkdir(directory, _DIR_MODE)
        except FileExistsError:
            pass
        except OSError as exc:
            errors.append(exc)
            continue

        try:
            info = os.lstat(directory)
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise PermissionError(f"admin socket runtime path is not a directory: {directory}")
            if hasattr(os, "getuid") and info.st_uid != uid:
                raise PermissionError(
                    f"admin socket runtime directory has the wrong owner: {directory}"
                )
            if stat.S_IMODE(info.st_mode) != _DIR_MODE:
                os.chmod(directory, _DIR_MODE)
            return directory
        except OSError as exc:
            errors.append(exc)

    cause = errors[-1] if errors else None
    raise OSError(
        errno.EACCES,
        "no private runtime directory is available for the admin socket",
    ) from cause


def _portable_socket_path(path: str) -> str:
    """Shorten an over-budget POSIX address without weakening its ACLs.

    Server and client may normalize the same requested address independently,
    so the replacement is deterministic. The digest prevents collisions while
    the per-uid ``0700`` directory, peer-credential check, and HMAC envelope
    retain the original defense-in-depth model.
    """
    if os.name == "nt" or len(os.fsencode(path)) <= _MAX_SOCKET_PATH_BYTES:
        return path

    uid = os.getuid() if hasattr(os, "getuid") else 0
    requested = os.path.abspath(os.path.expanduser(path))
    digest = hashlib.sha256(os.fsencode(requested)).hexdigest()[:24]
    directory = _private_short_socket_dir(uid)
    shortened = os.path.join(directory, f"{digest}.sock")
    if len(os.fsencode(shortened)) > _MAX_SOCKET_PATH_BYTES:
        raise OSError(
            errno.ENAMETOOLONG,
            "no safe admin socket address fits this platform's AF_UNIX limit",
        )
    logger.debug(
        "admin_unix_transport.path_shortened requested_bytes={} effective={}",
        len(os.fsencode(path)),
        shortened,
    )
    return shortened


# ---------------------------------------------------------------------
# Peer-credential reading (the SDDL-owner-ACE equivalent)
# ---------------------------------------------------------------------


def read_peer_uid(conn: socket.socket) -> int | None:
    """Return the uid of the process on the other end of ``conn``.

    Linux uses ``SO_PEERCRED`` (returns ``(pid, uid, gid)``); macOS/BSD use
    ``LOCAL_PEERCRED`` (``struct xucred``) and fall back to ``os.getpeereid``.
    Returns ``None`` when the platform exposes no peer-credential primitive
    (the caller then treats the peer as unverifiable and rejects it — fail
    closed). This function is intentionally small and side-effect-free so tests
    can monkeypatch it to simulate a mismatched-uid peer.
    """
    # Linux: SO_PEERCRED on SOL_SOCKET -> struct ucred { pid, uid, gid } (3 ints).
    so_peercred = getattr(socket, "SO_PEERCRED", None)
    if so_peercred is not None:
        try:
            buf = conn.getsockopt(socket.SOL_SOCKET, so_peercred, struct.calcsize("3i"))
            _pid, uid, _gid = struct.unpack("3i", buf)
            return int(uid)
        except (OSError, struct.error):  # pragma: no cover - platform/kernel guard
            return None

    # macOS / BSD: LOCAL_PEERCRED on SOL_LOCAL -> struct xucred; the uid is the
    # second 32-bit field (cr_version, cr_uid, ...). Prefer the simpler
    # os.getpeereid when available.
    getpeereid = getattr(os, "getpeereid", None)
    if getpeereid is not None:
        try:
            uid, _gid = getpeereid(conn.fileno())
            return int(uid)
        except (OSError, struct.error):  # pragma: no cover - platform/kernel guard
            return None

    local_peercred = getattr(socket, "LOCAL_PEERCRED", None)
    sol_local = getattr(socket, "SOL_LOCAL", 0)
    if local_peercred is not None:
        try:
            # struct xucred: u_int cr_version; uid_t cr_uid; ... — the uid is the
            # second unsigned int. We only need the first two fields.
            buf = conn.getsockopt(sol_local, local_peercred, struct.calcsize("2I"))
            _version, uid = struct.unpack("2I", buf)
            return int(uid)
        except (OSError, struct.error):  # pragma: no cover - platform/kernel guard
            return None

    return None


def default_socket_path() -> str:
    """Return the per-uid socket path (``$XDG_RUNTIME_DIR/jarvis-admin-<uid>.sock``).

    Falls back to ``/run/user/<uid>/`` and finally a ``0700`` ``mkdtemp`` when
    ``$XDG_RUNTIME_DIR`` is absent (the headless / no-systemd case). The
    directory is created ``0700`` if missing.
    """
    uid = os.getuid() if hasattr(os, "getuid") else 0
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime_dir:
        candidate = f"/run/user/{uid}"
        runtime_dir = candidate if os.path.isdir(candidate) else None
    if not runtime_dir:
        runtime_dir = tempfile.mkdtemp(prefix="jarvis-admin-", suffix=f"-{uid}")
    else:
        os.makedirs(runtime_dir, mode=_DIR_MODE, exist_ok=True)
    return os.path.join(runtime_dir, f"jarvis-admin-{uid}.sock")


# ---------------------------------------------------------------------
# UnixSocketTransport
# ---------------------------------------------------------------------


class UnixSocketTransport:
    """``AdminTransport`` over an ``AF_UNIX`` ``SOCK_STREAM`` socket (AD-12).

    The same object can serve and roundtrip in a loopback test. The server side
    binds ``socket_path`` ``0600`` in a ``0700`` dir, peer-cred-checks each
    accepted connection against the server uid, then runs the bytes handler
    (the reused ``ipc.AdminPipeServer.handle_raw`` chain). The client side
    connects, writes the signed envelope, and reads the response.
    """

    def __init__(
        self,
        socket_path: str | None = None,
        *,
        connect_timeout_s: float = 5.0,
    ) -> None:
        # Resolve lazily on first use on POSIX; on Windows ``default_socket_path``
        # would call ``os.getuid`` (absent) — but the factory never builds this
        # transport on Windows, and an explicit ``socket_path`` is always usable.
        self._socket_path = socket_path
        self._connect_timeout_s = connect_timeout_s
        self._server_uid = os.getuid() if hasattr(os, "getuid") else 0
        self._stop = asyncio.Event()
        self._server: asyncio.AbstractServer | None = None
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def address(self) -> str:
        """The effective socket path used by the elevator and helper."""
        if self._socket_path is None:
            self._socket_path = default_socket_path()
        self._socket_path = _portable_socket_path(self._socket_path)
        return self._socket_path

    # ------------------------------------------------------------------
    # Server
    # ------------------------------------------------------------------

    async def serve(self, handler: RequestHandler) -> None:
        """Bind the socket 0600/0700, accept forever, peer-cred-check each peer."""
        path = self.address
        # A stale socket file from a crashed previous run blocks bind. unlink()
        # raises FileNotFoundError (an OSError) when absent, so no exists() probe
        # is needed — that also keeps this off ASYNC240's os.path radar.
        try:
            os.unlink(path)
        except OSError:  # pragma: no cover - best-effort cleanup
            pass

        # Bind with a tight umask so the socket file lands 0600 (the umask masks
        # group/other bits at creation), then chmod to be explicit.
        old_umask = os.umask(0o077)
        try:
            self._server = await asyncio.start_unix_server(
                lambda r, w: self._on_client(r, w, handler), path=path
            )
        finally:
            os.umask(old_umask)
        try:
            os.chmod(path, _SOCK_MODE)
        except OSError:  # pragma: no cover - platform guard
            logger.warning("admin_unix_transport.chmod_failed", path=path)

        logger.info("admin_unix_transport.start", path=path, uid=self._server_uid)
        try:
            async with self._server:
                await self._stop.wait()
        finally:
            await self._drain_tasks()
            self._cleanup_socket(path)
            logger.info("admin_unix_transport.stopped")

    async def _on_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        handler: RequestHandler,
    ) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        try:
            # Peer-credential gate (the SDDL-owner-ACE equivalent) — fail closed.
            if not self._peer_uid_ok(writer):
                logger.warning("admin_unix_transport.peer_rejected")
                return
            raw = await self._read_message(reader)
            if raw is None:
                return
            try:
                payload = await handler(raw)
            except Exception:  # noqa: BLE001
                logger.exception("admin_unix_transport.handler_crashed")
                return
            writer.write(payload)
            await writer.drain()
        except (ConnectionError, OSError) as exc:
            logger.warning("admin_unix_transport.io_error", error=str(exc))
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except (ConnectionError, OSError):  # pragma: no cover
                pass

    def _peer_uid_ok(self, writer: asyncio.StreamWriter) -> bool:
        """True iff the connecting peer's uid == the server uid (fail closed)."""
        sock = writer.get_extra_info("socket")
        if sock is None:  # pragma: no cover - asyncio always exposes it
            return False
        peer_uid = read_peer_uid(sock)
        if peer_uid is None:
            # No peer-credential primitive on this host -> cannot verify -> reject.
            return False
        return peer_uid == self._server_uid

    @staticmethod
    async def _read_message(reader: asyncio.StreamReader) -> bytes | None:
        """Read the full request: client half-closes its write end at EOF.

        The client sends exactly one envelope then ``write_eof()``s, so reading
        to EOF yields the whole message. Caps at ``_MAX_PAYLOAD_BYTES``.
        """
        chunks: list[bytes] = []
        total = 0
        while True:
            data = await reader.read(65536)
            if not data:
                break
            chunks.append(data)
            total += len(data)
            if total > _MAX_PAYLOAD_BYTES:
                logger.warning("admin_unix_transport.payload_too_large")
                return None
        if not chunks:
            return None
        return b"".join(chunks)

    async def _drain_tasks(self) -> None:
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    @staticmethod
    def _cleanup_socket(path: str) -> None:
        try:
            if os.path.exists(path):
                os.unlink(path)
        except OSError:  # pragma: no cover
            pass

    def stop(self) -> None:
        """Signal the accept-loop to exit; the socket file is unlinked on drain."""
        self._stop.set()

    # ------------------------------------------------------------------
    # Client
    # ------------------------------------------------------------------

    async def roundtrip(self, raw: bytes) -> bytes:
        """Connect, write ``raw``, half-close, read the response to EOF."""
        path = self.address
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(path=path),
                timeout=self._connect_timeout_s,
            )
        except (FileNotFoundError, ConnectionRefusedError) as exc:
            # No helper bound yet — surface as the same FileNotFoundError the
            # named-pipe client raises so AdminPipeClient maps it to
            # "helper_unavailable".
            raise FileNotFoundError(f"admin socket {path} not available") from exc

        try:
            writer.write(raw)
            await writer.drain()
            if writer.can_write_eof():
                writer.write_eof()
            chunks: list[bytes] = []
            total = 0
            while True:
                data = await reader.read(65536)
                if not data:
                    break
                chunks.append(data)
                total += len(data)
                if total > _MAX_PAYLOAD_BYTES:
                    raise OSError("response exceeds limit")
            return b"".join(chunks)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except (ConnectionError, OSError):  # pragma: no cover
                pass


__all__ = [
    "UnixSocketTransport",
    "read_peer_uid",
    "default_socket_path",
    "RequestHandler",
]
