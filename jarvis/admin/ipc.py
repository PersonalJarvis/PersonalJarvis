r"""Admin-IPC core: HMAC/envelope/nonce/timestamp + the transport-agnostic
request/response handler (ADR-0001, superseded by ADR-0020).

Each request is HMAC-SHA256-signed (Nonce||Timestamp||OpType||OpId||Args) and the
server keeps an LRU of recently-seen ``(nonce, timestamp_ns)`` keys — replay runs
into the wall. A 30s timestamp window hard-rejects stale requests.

**This module is the security core and is transport-agnostic (AD-12).** It owns
``_canonical_args_json`` / ``_compute_hmac`` / ``_decode_request`` (the 5-step
check ordering) / ``_encode_response`` / the nonce LRU — all reused verbatim by
every transport. The byte pipe itself (Windows Named Pipe, Unix domain socket)
lives behind the :class:`~jarvis.admin.transport.AdminTransport` seam in
``transport.py`` / ``unix_socket.py``. ``AdminPipeServer`` / ``AdminPipeClient``
remain as thin Windows-named-pipe wrappers that bolt the reused core onto a
:class:`~jarvis.admin.transport.NamedPipeTransport`.

Import-cleanliness contract (HN-7): this module imports **no** Windows-only
package, not even lazily — the ``win32*`` / ``pywintypes`` imports moved into
``transport.py``. ``import jarvis.admin.ipc`` therefore succeeds on a Linux/macOS
VPS. Tests verify HMAC + nonce on the bytes level via ``_decode_request`` /
``_encode_response`` without any real pipe/socket connection.
"""
from __future__ import annotations

import asyncio
import collections
import hashlib
import hmac
import json
import secrets
import time
from typing import TYPE_CHECKING, Any

from loguru import logger
from pydantic import TypeAdapter, ValidationError

from .schema import AdminOperation, AdminResponse
from .transport import current_user_sid

if TYPE_CHECKING:
    from .executor import AdminExecutor
    from .transport import AdminTransport

# ---------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------

# Time window for valid timestamps (nanoseconds).
_TIMESTAMP_WINDOW_NS = 30 * 1_000_000_000
# Max nonces the helper remembers (FIFO-LRU).
# H5-Fix: the old limit of 256 was trivially exhaustible within the 30s window
# (~8 req/s was enough). New limit: 10_000 (rough upper bound for localhost
# traffic) AND the cache key is now (nonce, timestamp_ns) — a replay must
# reproduce both, which already fails outside the 30s window.
_NONCE_LRU_SIZE = 10_000
# Max-Payload (12 MB; 10 MB content_b64 is the cap at write_protected_path, plus
# envelope). The transport-side pipe buffers (``_PIPE_IN_BUFFER`` /
# ``_PIPE_OUT_BUFFER``) moved to ``transport.py`` alongside the relocated pipe
# code; this core only needs the payload-size guard in ``_decode_request``.
_MAX_PAYLOAD_BYTES = 12 * 1024 * 1024

_ADMIN_OP_ADAPTER: TypeAdapter[AdminOperation] = TypeAdapter(AdminOperation)


# ---------------------------------------------------------------------
# HMAC / payload serialization
# ---------------------------------------------------------------------

def _canonical_args_json(op_dump: dict[str, Any]) -> str:
    """Builds a deterministic JSON representation of the args for HMAC signing.

    ``op_id`` and ``type`` are packed as separate fields in the HMAC
    composition so that an attacker cannot break the signature by reordering
    the keys.
    """
    args = {k: v for k, v in op_dump.items() if k not in ("op_id", "type")}
    return json.dumps(args, sort_keys=True, separators=(",", ":"), default=str)


def _compute_hmac(secret: bytes, nonce: str, timestamp_ns: int,
                  op_type: str, op_id: str, args_json: str) -> str:
    """HMAC-SHA256 over all security-relevant fields.

    Why all of them? Because otherwise, for example, ``op_id`` could be
    changed without breaking the signature, and we correlate
    ``AdminOperationCompleted`` / ``AdminOperationRequested`` via it
    (flight-recorder replay).
    """
    msg = f"{nonce}|{timestamp_ns}|{op_type}|{op_id}|{args_json}".encode()
    return hmac.new(secret, msg, hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------
# Pipe names & secret handling
# ---------------------------------------------------------------------
#
# ``current_user_sid`` / ``default_pipe_name`` / ``_build_sddl`` were relocated
# to ``transport.py`` (they wrap Windows-only ``win32*`` APIs, HN-7). They are
# re-imported above so existing callers (``helper.py``, ``client.py``, tests)
# keep importing them from ``jarvis.admin.ipc`` unchanged.


# ---------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------

class AdminPipeServer:
    """Server side of the admin IPC: reused HMAC core + a pluggable transport.

    Lifecycle:
    1. ``__init__(secret, pipe_name, executor)`` — configuration; no bind yet.
    2. ``await serve_forever()`` — accepts connections in an infinite loop.
    3. ``stop()`` — sets the shutdown flag; the next accept-loop iteration exits.

    The byte transport is the :class:`~jarvis.admin.transport.AdminTransport`
    seam: by default the Windows ``NamedPipeTransport`` bound to ``pipe_name`` +
    ``sid``, but a ``UnixSocketTransport`` (or a fake) can be injected via
    ``transport=...`` so the same HMAC/executor chain serves any OS (AD-12). The
    transport runs the blocking accept/read/write in worker threads; one task
    per connection means a slow ``install_winget`` does not stall accept.
    """

    def __init__(
        self,
        secret: bytes,
        pipe_name: str,
        executor: AdminExecutor,
        *,
        sid: str | None = None,
        transport: AdminTransport | None = None,
    ) -> None:
        if not secret or len(secret) < 16:
            raise ValueError("secret muss mindestens 16 Bytes sein")
        self._secret = secret
        self._pipe_name = pipe_name
        self._executor = executor
        self._sid = sid or current_user_sid()
        self._transport = transport
        # H5-Fix: cache key is now ``(nonce, timestamp_ns)`` — a replay must
        # reproduce both. Outside the 30s window the timestamp check already
        # fails; inside it the combination is unique per request.
        self._nonce_cache: collections.deque[tuple[str, int]] = collections.deque(
            maxlen=_NONCE_LRU_SIZE
        )
        self._nonce_set: set[tuple[str, int]] = set()
        self._stop = asyncio.Event()
        self._tasks: set[asyncio.Task[None]] = set()

    # ------------------------------------------------------------------
    # Payload-Verifikation (Unit-Test-friendly)
    # ------------------------------------------------------------------

    def _check_and_record_nonce(self, nonce: str, timestamp_ns: int) -> bool:
        """Checks whether (nonce, timestamp) has already been seen; records it if not."""
        key = (nonce, timestamp_ns)
        if key in self._nonce_set:
            return False
        # FIFO: when full, deque.append evicts the oldest entry — keep the set in sync.
        if len(self._nonce_cache) == _NONCE_LRU_SIZE:
            evicted = self._nonce_cache[0]
            self._nonce_set.discard(evicted)
        self._nonce_cache.append(key)
        self._nonce_set.add(key)
        return True

    def _decode_request(self, raw: bytes) -> tuple[AdminOperation, str]:
        """Deserializes and verifies HMAC + nonce + timestamp.

        Check order (H4-Fix):
            1. Envelope format
            2. Timestamp window
            3. HMAC verify over the RAW op payload (not the Pydantic-normalized
               form) — so the signature is independent of schema validation and
               an attacker cannot build an oracle from ``schema_invalid`` vs.
               ``hmac_invalid``.
            4. Nonce replay
            5. Pydantic validation

        Returns (AdminOperation, nonce) on success. On failure:
        raises ``_AuthFailure`` (internal). Exposed as public for tests.
        """
        if len(raw) > _MAX_PAYLOAD_BYTES:
            raise _AuthFailure("payload_too_large")
        try:
            envelope = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _AuthFailure("json_parse_error") from exc
        if not isinstance(envelope, dict):
            raise _AuthFailure("envelope_not_object")

        nonce = envelope.get("nonce")
        ts = envelope.get("timestamp_ns")
        sig = envelope.get("hmac")
        op_payload = envelope.get("op")

        if not (isinstance(nonce, str) and isinstance(ts, int)
                and isinstance(sig, str) and isinstance(op_payload, dict)):
            raise _AuthFailure("envelope_fields_invalid")

        # 2. Timestamp-Window
        now_ns = time.time_ns()
        if abs(now_ns - ts) > _TIMESTAMP_WINDOW_NS:
            raise _AuthFailure("timestamp_out_of_window")

        # 3. HMAC verify over the raw op_payload. We read op_type and op_id
        # directly from the dict; if fields are missing the HMAC comparison
        # will definitely fail — no oracle.
        raw_op_type = op_payload.get("type", "")
        raw_op_id = str(op_payload.get("op_id", ""))
        if not isinstance(raw_op_type, str):
            raise _AuthFailure("hmac_invalid")
        args_json = _canonical_args_json(op_payload)
        expected = _compute_hmac(self._secret, nonce, ts,
                                  raw_op_type, raw_op_id, args_json)
        if not hmac.compare_digest(expected, sig):
            raise _AuthFailure("hmac_invalid")

        # 4. Nonce replay (cache key is (nonce, ts), see _check_and_record_nonce).
        if not self._check_and_record_nonce(nonce, ts):
            raise _AuthFailure("nonce_replay")

        # 5. Now, after HMAC + nonce, Pydantic validation is safe.
        try:
            op: AdminOperation = _ADMIN_OP_ADAPTER.validate_python(op_payload)
        except ValidationError as exc:
            raise _AuthFailure(f"schema_invalid:{exc.error_count()}") from exc

        return op, nonce

    def _encode_response(self, resp: AdminResponse) -> bytes:
        """Serializes the response as JSON bytes (no HMAC — the client trusts
        the helper process; the IPC channel itself is already protected by the
        pipe ACL and the process context)."""
        return resp.model_dump_json().encode("utf-8")

    # ------------------------------------------------------------------
    # Transport-agnostic request handler (the reused core seam)
    # ------------------------------------------------------------------

    async def handle_raw(self, raw: bytes) -> bytes:
        """Bytes-level handler: decode -> execute -> encode.

        This is exactly the ``handler: Callable[[bytes], Awaitable[bytes]]`` the
        :class:`~jarvis.admin.transport.AdminTransport` seam serves. It is
        transport-agnostic — the named pipe and the unix socket both feed raw
        envelopes through here unchanged (AD-12). HMAC/nonce/timestamp/schema
        validation lives in ``_decode_request``; the executor and the structured
        ``AdminResponse`` shapes are identical regardless of transport.
        """
        start_ns = time.time_ns()
        try:
            op, nonce = self._decode_request(raw)
        except _AuthFailure as exc:
            logger.warning("admin_pipe_server.auth_fail", reason=str(exc))
            # Structured error response with an empty UUID — the client can
            # react based on ``success=False`` + error_code.
            from uuid import UUID as _UUID
            resp = AdminResponse(
                op_id=_UUID(int=0), success=False,
                error_code=str(exc), error_message="Request rejected",
            )
            return self._encode_response(resp)
        del nonce  # only needed for audit purposes

        logger.info("admin_pipe_server.op_received",
                    op_id=str(op.op_id), op_type=op.type)
        try:
            resp = await self._executor.execute(op)
        except Exception as exc:  # noqa: BLE001
            logger.exception("admin_pipe_server.execute_crashed",
                             op_id=str(op.op_id))
            resp = AdminResponse(
                op_id=op.op_id, success=False,
                error_code="executor_crashed",
                error_message=f"{type(exc).__name__}: {exc}",
                duration_ms=max(0, (time.time_ns() - start_ns) // 1_000_000),
            )
        return self._encode_response(resp)

    # ------------------------------------------------------------------
    # Accept-Loop (delegated to the transport seam)
    # ------------------------------------------------------------------

    def _ensure_transport(self) -> AdminTransport:
        if self._transport is None:
            # Default: the Windows named-pipe transport bound to this server's
            # pipe + SID. An injected transport (tests / a future unix helper)
            # overrides this via ``transport=...`` in ``__init__``.
            from .transport import NamedPipeTransport
            self._transport = NamedPipeTransport(self._pipe_name, sid=self._sid)
        return self._transport

    async def serve_forever(self) -> None:
        """Runs until ``stop()`` is called. Each connection gets its own task.

        Delegates the byte transport (accept/read/write/close) to the
        :class:`~jarvis.admin.transport.AdminTransport` seam; the reused HMAC/
        executor chain runs in :meth:`handle_raw`.
        """
        transport = self._ensure_transport()
        await transport.serve(self.handle_raw)

    def stop(self) -> None:
        """Signals the transport accept loop to exit cleanly."""
        self._stop.set()
        if self._transport is not None:
            self._transport.stop()


class _AuthFailure(Exception):
    """Internal marker: HMAC / nonce / timestamp / schema verification failed."""


# ---------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------

class AdminPipeClient:
    """Client side of the admin IPC: signs the envelope, delegates the byte
    roundtrip to the transport seam, parses the response.

    No connection pool: admin ops are infrequent and the pipe only has a single
    max-instance anyway. Each ``send()`` opens a fresh connection. The byte transport
    is the :class:`~jarvis.admin.transport.AdminTransport` seam — by default a
    Windows ``NamedPipeTransport`` bound to ``pipe_name``, but injectable
    (``transport=...``) so the same HMAC envelope build serves any OS (AD-12).
    """

    def __init__(self, secret: bytes, pipe_name: str,
                 *, connect_timeout_ms: int = 5000,
                 io_timeout_s: float = 180.0,
                 transport: AdminTransport | None = None) -> None:
        if not secret or len(secret) < 16:
            raise ValueError("secret muss mindestens 16 Bytes sein")
        self._secret = secret
        self._pipe_name = pipe_name
        self._connect_timeout_ms = connect_timeout_ms
        self._io_timeout_s = io_timeout_s
        self._transport = transport

    def _ensure_transport(self) -> AdminTransport:
        if self._transport is None:
            from .transport import NamedPipeTransport
            self._transport = NamedPipeTransport(
                self._pipe_name, connect_timeout_ms=self._connect_timeout_ms
            )
        return self._transport

    async def send(self, op: AdminOperation) -> AdminResponse:
        """Signs the op, opens the pipe, writes the request, reads the response."""
        envelope = self._build_envelope(op)
        raw = json.dumps(envelope).encode("utf-8")
        transport = self._ensure_transport()

        try:
            resp_bytes = await asyncio.wait_for(
                transport.roundtrip(raw),
                timeout=self._io_timeout_s,
            )
        except TimeoutError:
            return AdminResponse(
                op_id=op.op_id, success=False,
                error_code="ipc_timeout",
                error_message="Admin-Helper antwortete nicht innerhalb der Frist.",
            )
        except FileNotFoundError:
            return AdminResponse(
                op_id=op.op_id, success=False,
                error_code="helper_unavailable",
                error_message=f"Named-Pipe {self._pipe_name} existiert nicht.",
            )
        except Exception as exc:  # noqa: BLE001
            return AdminResponse(
                op_id=op.op_id, success=False,
                error_code="ipc_error",
                error_message=f"{type(exc).__name__}: {exc}",
            )

        try:
            data = json.loads(resp_bytes.decode("utf-8"))
            return AdminResponse.model_validate(data)
        except Exception as exc:  # noqa: BLE001
            return AdminResponse(
                op_id=op.op_id, success=False,
                error_code="response_parse_error",
                error_message=f"{type(exc).__name__}: {exc}",
            )

    def _build_envelope(self, op: AdminOperation) -> dict[str, Any]:
        """HMAC envelope as specified by ADR-0001."""
        op_dump = op.model_dump(mode="json")
        op_type = op_dump["type"]
        op_id = op_dump["op_id"]
        nonce = secrets.token_hex(16)
        ts = time.time_ns()
        args_json = _canonical_args_json(op_dump)
        sig = _compute_hmac(self._secret, nonce, ts, op_type, op_id, args_json)
        return {
            "nonce": nonce,
            "timestamp_ns": ts,
            "hmac": sig,
            "op": op_dump,
        }
