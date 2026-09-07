"""The host↔screen wire protocol, and the two transports that carry it.

A screen runs an in-session *runner* — a small program living INSIDE the
isolated session — because that is the only place from which capture and
synthetic input reach that session's own input stream. The host drives it
with five verbs (``health``, ``geometry``, ``foreground``, ``grab``, ``act``,
plus ``quit``), each a JSON request answered by a JSON envelope and, for
``grab``, a raw pixel payload.

Two transports carry the same verbs:

* :class:`HttpTransport` — loopback HTTP. Used where the runner shares the
  host's network namespace (a virtual X display, the diagnostic attached
  runner).
* :class:`MailboxTransport` — request/response FILES in a folder both sides
  can see. Used for Windows Sandbox, where the guest sits behind NAT and the
  host cannot dial into it. It is not a workaround but the better default
  there: it works with the sandbox's networking switched OFF entirely, so an
  agent screen needs no network attack surface at all.

The framing is deliberately primitive (JSON + one raw blob, fixed file
names). The Windows Sandbox guest has no Python — its runner is PowerShell
with inline C# — so every rule here must be re-implementable in a page of
another language without a parser.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Bumped when the verbs or framing change incompatibly. The host refuses a
#: runner whose major differs rather than mis-parsing its answers.
PROTOCOL_VERSION = 1

#: Header/field names shared by both transports.
HEADER_TOKEN = "X-Agent-Screen-Token"  # noqa: S105 — a header NAME, not a secret
HEADER_WIDTH = "X-Agent-Screen-Width"
HEADER_HEIGHT = "X-Agent-Screen-Height"
HEADER_FORMAT = "X-Agent-Screen-Format"

#: Mailbox file suffixes. The response JSON is always written LAST, after any
#: binary payload, so its mere existence proves the whole answer is on disk —
#: the one ordering rule the PowerShell runner must also honour.
REQ_SUFFIX = ".req.json"
RES_SUFFIX = ".res.json"
BIN_SUFFIX = ".res.bin"
PART_SUFFIX = ".part"

#: How often the mailbox host polls for a response. 15 ms is well under one
#: display frame, so it never dominates a capture that already costs
#: milliseconds, while keeping the guest's own poll loop cheap.
MAILBOX_POLL_S = 0.015


class TransportError(RuntimeError):
    """The runner could not be reached, or answered unusably."""


def write_atomic(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` so a reader never sees a partial file.

    ``os.replace`` is atomic within one directory on NTFS and POSIX alike,
    which is what makes the mailbox safe without any locking: a reader either
    sees the previous state or the complete new file, never a half-written
    one. Mapped Windows Sandbox folders are ordinary filesystem redirects, so
    the guarantee survives the guest boundary.
    """
    tmp = path.with_name(path.name + PART_SUFFIX)
    tmp.write_bytes(data)
    os.replace(tmp, path)


def encode_request(method: str, payload: dict[str, Any] | None = None) -> bytes:
    return json.dumps(
        {"v": PROTOCOL_VERSION, "method": method, "params": payload or {}},
        ensure_ascii=False,
    ).encode("utf-8")


def decode_envelope(raw: bytes) -> dict[str, Any]:
    try:
        obj = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TransportError(f"screen runner sent unparseable JSON: {exc}") from exc
    if not isinstance(obj, dict):
        raise TransportError("screen runner sent a non-object envelope")
    return obj


class Transport:
    """Host-side channel to one runner."""

    def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout_s: float = 20.0,
    ) -> tuple[dict[str, Any], bytes]:
        """Send one verb; return ``(json envelope, binary payload)``."""
        raise NotImplementedError

    def close(self) -> None:
        """Release transport resources. Idempotent, never raises."""


class HttpTransport(Transport):
    """Loopback HTTP with a bearer token and a kept-alive connection.

    Keep-alive is not a micro-optimisation here: one perception frame issues
    at least two grabs plus foreground reads, and a fresh TCP handshake per
    grab measurably lengthened the stability loop in the local rig. The
    connection is guarded by a lock because the CU engine reaches a screen
    from ``asyncio.to_thread`` workers, i.e. from more than one thread.
    """

    def __init__(self, host: str, port: int, token: str) -> None:
        self._host = host
        self._port = int(port)
        self._token = token
        self._conn: Any = None
        import threading

        self._lock = threading.Lock()

    def _connection(self, timeout_s: float) -> Any:
        import http.client  # noqa: PLC0415

        if self._conn is None:
            self._conn = http.client.HTTPConnection(
                self._host,
                self._port,
                timeout=timeout_s,
            )
        return self._conn

    def _drop(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001 — teardown is best-effort
                logger.debug("agent-screen transport close failed", exc_info=True)

    def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout_s: float = 20.0,
    ) -> tuple[dict[str, Any], bytes]:
        body = encode_request(method, params)
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            HEADER_TOKEN: self._token,
            "Connection": "keep-alive",
        }
        with self._lock:
            for attempt in (1, 2):
                try:
                    conn = self._connection(timeout_s)
                    conn.request("POST", f"/{method}", body=body, headers=headers)
                    response = conn.getresponse()
                    raw = response.read()
                    if response.status == 401:
                        raise TransportError("screen runner rejected the token")
                    if response.status != 200:
                        raise TransportError(
                            f"screen runner returned HTTP {response.status} for {method}",
                        )
                    content_type = response.getheader("Content-Type", "")
                    if "application/octet-stream" in content_type:
                        envelope = {
                            "ok": True,
                            "width": int(response.getheader(HEADER_WIDTH, "0") or 0),
                            "height": int(response.getheader(HEADER_HEIGHT, "0") or 0),
                            "format": response.getheader(HEADER_FORMAT, "RGB"),
                        }
                        return envelope, raw
                    return decode_envelope(raw), b""
                except TransportError:
                    raise
                except Exception as exc:  # noqa: BLE001 — a stale keep-alive
                    # socket surfaces as an arbitrary OSError/http exception on
                    # the FIRST use after the peer recycled it. Retry once on a
                    # fresh connection; a second failure is a real outage.
                    self._drop()
                    if attempt == 2:
                        raise TransportError(
                            f"screen runner unreachable at "
                            f"{self._host}:{self._port} ({exc})",
                        ) from exc
        raise TransportError("unreachable")  # pragma: no cover — loop always returns

    def close(self) -> None:
        with self._lock:
            self._drop()


class MailboxTransport(Transport):
    """Request/response files in a directory both sides can see.

    Chosen for Windows Sandbox: the guest is NAT'd, so the host cannot dial
    it, and a mapped folder is the one channel the sandbox configuration
    guarantees. Because it needs no network at all, the sandbox can be booted
    with networking disabled — the agent screen then has a strictly smaller
    attack surface than the user's own desktop.
    """

    def __init__(self, mailbox_dir: Path, token: str) -> None:
        self._dir = Path(mailbox_dir)
        self._token = token
        self._dir.mkdir(parents=True, exist_ok=True)
        import threading

        self._lock = threading.Lock()

    def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout_s: float = 20.0,
    ) -> tuple[dict[str, Any], bytes]:
        seq = uuid.uuid4().hex[:16]
        req_path = self._dir / f"{seq}{REQ_SUFFIX}"
        res_path = self._dir / f"{seq}{RES_SUFFIX}"
        bin_path = self._dir / f"{seq}{BIN_SUFFIX}"
        request = {
            "v": PROTOCOL_VERSION,
            "token": self._token,
            "method": method,
            "params": params or {},
        }
        with self._lock:
            try:
                write_atomic(
                    req_path,
                    json.dumps(request, ensure_ascii=False).encode("utf-8"),
                )
            except OSError as exc:
                raise TransportError(
                    f"cannot write to the screen mailbox {self._dir}: {exc}",
                ) from exc
            deadline = time.monotonic() + max(0.1, timeout_s)
            try:
                while True:
                    if res_path.exists():
                        break
                    if time.monotonic() >= deadline:
                        raise TransportError(
                            f"the screen runner did not answer {method!r} within "
                            f"{timeout_s:.0f}s — the session may have died",
                        )
                    time.sleep(MAILBOX_POLL_S)
                envelope = decode_envelope(res_path.read_bytes())
                blob = b""
                if envelope.get("binary"):
                    blob = bin_path.read_bytes()
            finally:
                for stale in (req_path, res_path, bin_path):
                    try:
                        stale.unlink(missing_ok=True)
                    except OSError:  # noqa: PERF203 — cleanup is best-effort
                        pass
        if envelope.get("ok") is False and method != "act":
            raise TransportError(
                f"screen runner failed {method!r}: {envelope.get('error', '')}",
            )
        return envelope, blob

    def close(self) -> None:
        """Nothing to release — the files are removed per call."""


__all__ = [
    "BIN_SUFFIX",
    "HEADER_FORMAT",
    "HEADER_HEIGHT",
    "HEADER_TOKEN",
    "HEADER_WIDTH",
    "HttpTransport",
    "MAILBOX_POLL_S",
    "MailboxTransport",
    "PART_SUFFIX",
    "PROTOCOL_VERSION",
    "REQ_SUFFIX",
    "RES_SUFFIX",
    "Transport",
    "TransportError",
    "decode_envelope",
    "encode_request",
    "write_atomic",
]
