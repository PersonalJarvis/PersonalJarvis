"""Parent-side AdminClient — the interface seen by tools and the brain.

Responsibilities:

1. **Secret resolution**: on the first ``execute()`` call the HMAC secret is
   fetched from the Credential Manager (keyring service = ``personal-jarvis``,
   key = ``jarvis_admin_hmac``). If none is present → auto-generation
   (via the ``launcher.py`` path). This module assumes the secret exists — if
   not, ``execute`` fails with ``error_code="no_secret"``.
2. **Destructiveness check**: ops in ``DESTRUCTIVE_OPS`` are not sent
   spontaneously. The caller must pass ``destructive_approved=True`` explicitly;
   otherwise ``DestructiveRequiresApproval`` is raised.
   The risk-tier system (``dispatch_to_admin`` tool) triggers a user prompt
   on that exception.
3. **Bus events**: ``AdminOperationRequested`` before send,
   ``AdminOperationCompleted`` on success, ``AdminOperationRejected``
   on HMAC error, destructive rejection, or cancel-token trip.
4. **Cancel-token respect**: if the token is already ``is_cancelled()`` before
   the send → immediate ``ipc_cancelled`` response, no round-trip to the pipe.
"""
from __future__ import annotations

import base64
from typing import TYPE_CHECKING

from loguru import logger

from jarvis.core.config import KEYRING_SERVICE, get_secret
from jarvis.core.events import (
    AdminOperationCompleted,
    AdminOperationRejected,
    AdminOperationRequested,
)

from .elevator import Elevator, make_elevator
from .ipc import AdminPipeClient
from .schema import DESTRUCTIVE_OPS, AdminOperation, AdminResponse
from .transport import default_pipe_name, make_admin_transport

if TYPE_CHECKING:
    from jarvis.control.cancel import CancelToken
    from jarvis.core.bus import EventBus


ADMIN_HMAC_KEY = "jarvis_admin_hmac"
ADMIN_HMAC_ENV = "JARVIS_ADMIN_HMAC"


class DestructiveRequiresApproval(Exception):
    """Raised when a destructive op arrives without explicit approval.

    The risk-tier system catches this exception and triggers a user prompt.
    On a subsequent call with ``destructive_approved=True`` the op proceeds.
    """

    def __init__(self, op: AdminOperation) -> None:
        super().__init__(
            f"Op '{op.type}' ist destruktiv und braucht explizite Zustimmung."
        )
        self.op = op
        self.op_id = str(op.op_id)
        self.op_type = op.type


class AdminClient:
    """High-level Interface: ``AdminClient(bus, cancel_token).execute(op)``.

    Die Transport-Verbindung wird lazy beim ersten Call aufgebaut. ``AdminClient``
    ist bewusst leichtgewichtig — kein Connection-Pool, da Admin-Ops selten
    sind. The byte transport is selected per OS via ``make_admin_transport()``
    (Windows named pipe / Unix domain socket); the elevation mechanism is
    selected per OS via ``make_elevator()`` (UAC / polkit / sudo / osascript /
    Null). Both are injectable for tests (``pipe_client=`` / ``elevator=``),
    preserving the DI seam (PC-3).
    """

    def __init__(
        self,
        bus: EventBus | None = None,
        cancel_token: CancelToken | None = None,
        *,
        pipe_name: str | None = None,
        pipe_client: AdminPipeClient | None = None,
        elevator: Elevator | None = None,
    ) -> None:
        self._bus = bus
        self._cancel_token = cancel_token
        self._pipe_name = pipe_name or default_pipe_name()
        self._pipe_client = pipe_client
        # Whether the transport client was injected (tests / an already-bound
        # helper) vs lazily built from a secret. An injected client means the
        # byte transport is already serviceable, so the elevation gate is moot.
        self._injected_pipe_client = pipe_client is not None
        # AD-6: an unavailable mechanism is the NullElevator, which refuses
        # gracefully at call time — never a crash. make_elevator() never raises.
        self._elevator = elevator if elevator is not None else make_elevator()

    # ------------------------------------------------------------------
    # Secret
    # ------------------------------------------------------------------

    def _load_secret(self) -> bytes | None:
        """Load the HMAC secret from the Credential Manager.

        The secret is base64-URL-safe encoded (as stored by the launcher).
        Returns ``None`` if absent — the launcher is the only place that may
        generate a new one.
        """
        raw = get_secret(ADMIN_HMAC_KEY, env_fallback=ADMIN_HMAC_ENV)
        if not raw:
            return None
        try:
            return base64.urlsafe_b64decode(raw.encode("ascii"))
        except Exception:  # noqa: BLE001
            # Non-base64 fallback: the user may have stored a raw value
            # — interpret it as UTF-8.
            return raw.encode("utf-8")

    def _ensure_transport(self) -> AdminPipeClient | None:
        """Build the parent-side IPC client over the OS-appropriate transport.

        The HMAC envelope build + response parsing live in ``AdminPipeClient``;
        only the byte transport swaps per OS via ``make_admin_transport()``
        (Windows named pipe / Unix domain socket). Returns ``None`` when no HMAC
        secret is configured — the caller surfaces that as a typed refusal.
        """
        if self._pipe_client is not None:
            return self._pipe_client
        secret = self._load_secret()
        if secret is None:
            return None
        transport = make_admin_transport(self._pipe_name)
        self._pipe_client = AdminPipeClient(
            secret, self._pipe_name, transport=transport
        )
        return self._pipe_client

    # ------------------------------------------------------------------
    # Main call
    # ------------------------------------------------------------------

    async def execute(
        self,
        op: AdminOperation,
        *,
        destructive_approved: bool = False,
    ) -> AdminResponse:
        """Send an AdminOperation to the helper.

        :param op: validated Pydantic object (already checked against the schema).
        :param destructive_approved: must be ``True`` for destructive ops;
            otherwise ``DestructiveRequiresApproval`` is raised.
        """
        op_id_str = str(op.op_id)

        # 1. Check destructiveness
        destructive = op.type in DESTRUCTIVE_OPS
        if destructive and not destructive_approved:
            await self._publish_rejected(op, reason="destructive_requires_approval")
            raise DestructiveRequiresApproval(op)

        # 2. Respect the cancel token
        if self._cancel_token is not None and self._cancel_token.is_cancelled():
            reason = self._cancel_token.reason or "cancelled"
            await self._publish_rejected(op, reason=f"cancelled:{reason}")
            return AdminResponse(
                op_id=op.op_id,
                success=False,
                error_code="cancelled",
                error_message=f"CancelToken gesetzt: {reason}",
            )

        # 3. Ensure transport client + elevation
        client = self._ensure_transport()
        if client is None:
            await self._publish_rejected(op, reason="no_secret")
            return AdminResponse(
                op_id=op.op_id,
                success=False,
                error_code="no_secret",
                error_message=(
                    "HMAC-Secret fehlt im Credential Manager — "
                    "Admin-Helper wurde noch nicht initialisiert."
                ),
            )
        # AD-6: when no elevation mechanism is available (NullElevator on a
        # headless box), refuse with the SAME typed-AdminResponse shape as the
        # no_secret path — never a crash, never a silent drop (AD-OE6). An
        # injected pipe_client (tests / an already-bound helper) skips this gate,
        # since the byte transport is already serviceable in that case.
        if self._pipe_client is client and self._injected_pipe_client:
            pass  # injected transport: elevation already provided out-of-band.
        elif not self._elevator.is_available():
            await self._publish_rejected(op, reason="no_elevation")
            return AdminResponse(
                op_id=op.op_id,
                success=False,
                error_code="no_elevation",
                error_message=(
                    "no elevation mechanism available on this host — "
                    "privileged operations are disabled; install pkexec or "
                    "run with sudo."
                ),
            )

        # 4. Publish the request event
        await self._publish_requested(op, destructive=destructive)

        # 5. Roundtrip
        logger.info("admin_client.send", op_id=op_id_str, op_type=op.type,
                    destructive=destructive)
        resp = await client.send(op)

        # 6. Completion / rejection event
        if resp.success:
            await self._publish_completed(op, resp)
        else:
            # Distinguish HMAC/schema rejects (from the server) from
            # execution errors. Both land in the same event type,
            # but ``reason`` carries the error_code.
            await self._publish_rejected(
                op, reason=resp.error_code or "unknown_error"
            )
        return resp

    # ------------------------------------------------------------------
    # Event helpers
    # ------------------------------------------------------------------

    async def _publish_requested(
        self, op: AdminOperation, *, destructive: bool
    ) -> None:
        if self._bus is None:
            return
        await self._bus.publish(
            AdminOperationRequested(
                op_id=str(op.op_id),
                op_type=op.type,
                destructive=destructive,
            )
        )

    async def _publish_completed(
        self, op: AdminOperation, resp: AdminResponse
    ) -> None:
        if self._bus is None:
            return
        await self._bus.publish(
            AdminOperationCompleted(
                op_id=str(op.op_id),
                op_type=op.type,
                success=resp.success,
                duration_ms=resp.duration_ms,
            )
        )

    async def _publish_rejected(
        self, op: AdminOperation, *, reason: str
    ) -> None:
        if self._bus is None:
            return
        await self._bus.publish(
            AdminOperationRejected(
                op_id=str(op.op_id),
                op_type=op.type,
                reason=reason,
            )
        )


# ---------------------------------------------------------------------
# Convenience: secret check only (used by the dispatch_to_admin tool)
# ---------------------------------------------------------------------

def admin_secret_configured() -> bool:
    """Return True if the Credential Manager contains an HMAC key."""
    return bool(get_secret(ADMIN_HMAC_KEY, env_fallback=ADMIN_HMAC_ENV))


__all__ = [
    "AdminClient",
    "DestructiveRequiresApproval",
    "ADMIN_HMAC_KEY",
    "ADMIN_HMAC_ENV",
    "KEYRING_SERVICE",
    "admin_secret_configured",
]
