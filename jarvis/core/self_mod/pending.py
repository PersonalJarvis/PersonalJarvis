"""PendingMutationStore — in-memory buffering of prepared mutations.

Plan reference: §7.3.

The `set_config_value` brain tool definition does NOT write directly to
`jarvis.toml`; it calls `PendingMutationStore.create(request)`.
Pending entries live for at most 5 minutes — after that the next tool call
cleans them up via `cleanup_expired()`.

SAFE-tier paths (Plan-§AD-10) are **automatically confirmed** by the store
without ever entering the pending bucket — this simplifies the voice
confirmation layer (Phase 7.4) to pure ASK-tier handling.

ASK-tier paths land in the pending bucket. The voice layer (Phase 7.4)
later calls `confirm(id)` or `reject(id)`.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, ClassVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .audit import SelfModAudit
from .errors import (
    AllowlistViolationError,
    PreValidateError,
    SecretAccessError,
)
from .registry import SelfModRegistry
from .schema import (
    AuditActor,
    AuditEvent,
    AuditSource,
    MutableSpec,
    MutationRequest,
    MutationResult,
)
from .writer import AtomicConfigWriter

_LOG = logging.getLogger(__name__)


class PendingMutation(BaseModel):
    """Tool output schema for `set_config_value` (Plan-§7.3)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    path: str
    old_value: Any = None
    new_value: Any = None
    needs_confirmation: bool
    risk_tier: str
    requires_restart: bool
    applied: bool
    backup_path: str | None = None
    description: str
    created_at: float = Field(default_factory=time.time)


@dataclass(slots=True)
class _PendingEntry:
    """Internal: bundles tool output and original request for a later confirm()."""

    pending: PendingMutation
    request: MutationRequest


class PendingMutationStore:
    """In-memory buffer with TTL and SAFE-tier auto-confirm.

    Plan API: `create / get / confirm / reject / cleanup_expired`.
    Thread-safe via an internal `threading.Lock`.
    """

    DEFAULT_TTL_SECONDS: ClassVar[float] = 300.0  # 5 minutes (Plan-§7.3)

    def __init__(
        self,
        *,
        writer: AtomicConfigWriter,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        auto_confirm_safe: bool = True,
        audit: SelfModAudit | None = None,
    ) -> None:
        self._writer = writer
        self._ttl_seconds = ttl_seconds
        self._auto_confirm_safe = auto_confirm_safe
        # Audit only for `reject()` — mutate audit comes from the writer.
        self._audit = audit if audit is not None else writer._audit  # noqa: SLF001
        self._entries: dict[UUID, _PendingEntry] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API (Plan-§7.3)
    # ------------------------------------------------------------------

    def create(self, request: MutationRequest) -> PendingMutation:
        """Prepare a mutation — or confirm immediately for SAFE-tier paths.

        Raises `AllowlistViolationError` / `SecretAccessError` from the
        allowlist check and (for SAFE auto-confirm) `PreValidateError` from
        the writer. The tool layer converts these into a
        `ToolResult(success=False, ...)` response.
        """
        spec: MutableSpec = SelfModRegistry.require_spec(request.path)
        old_value = self._read_old_value(request.path)

        if self._auto_confirm_safe and spec.risk_tier == "safe":
            # Plan-§AD-10 + §7.4 SAFE-tier path
            result: MutationResult = self._writer.mutate(request)
            return PendingMutation(
                id=request.correlation_id,
                path=request.path,
                old_value=old_value,
                new_value=request.new_value,
                needs_confirmation=False,
                risk_tier=spec.risk_tier,
                requires_restart=spec.needs_restart,
                applied=True,
                backup_path=result.backup_path,
                description=spec.description,
            )

        pending = PendingMutation(
            id=request.correlation_id,
            path=request.path,
            old_value=old_value,
            new_value=request.new_value,
            needs_confirmation=True,
            risk_tier=spec.risk_tier,
            requires_restart=spec.needs_restart,
            applied=False,
            backup_path=None,
            description=spec.description,
        )
        with self._lock:
            self._cleanup_expired_locked()
            self._entries[pending.id] = _PendingEntry(pending=pending, request=request)
        return pending

    def get(self, mutation_id: UUID) -> PendingMutation | None:
        """Return the pending entry — `None` if expired or unknown."""
        with self._lock:
            self._cleanup_expired_locked()
            entry = self._entries.get(mutation_id)
            return entry.pending if entry is not None else None

    def confirm(self, mutation_id: UUID) -> MutationResult:
        """Confirm an ASK-tier pending entry → calls `AtomicConfigWriter.mutate()`.

        Raises `KeyError` if the pending entry no longer exists
        (e.g. after TTL expiry or a duplicate `confirm`).
        """
        with self._lock:
            self._cleanup_expired_locked()
            entry = self._entries.pop(mutation_id, None)
        if entry is None:
            raise KeyError(
                f"Pending-Mutation {mutation_id} nicht gefunden (abgelaufen "
                f"oder bereits konsumiert)"
            )
        return self._writer.mutate(entry.request)

    def reject(
        self,
        mutation_id: UUID,
        *,
        reason: str = "rejected_by_user",
        source: AuditSource = AuditSource.UI,
        voice_confirmation: dict[str, Any] | None = None,
    ) -> None:
        """Discard a pending entry and write an audit trail. Idempotent.

        Phase 7.4 uses `reason="voice_vetoed"` and `reason="voice_timeout"`
        together with `voice_confirmation`={"transcript", "confidence",
        "timestamp_utc"} for the audit trail.
        """
        with self._lock:
            entry = self._entries.pop(mutation_id, None)
        if entry is None:
            return
        try:
            extras: dict[str, Any] = {}
            if voice_confirmation is not None:
                extras["voice_confirmation"] = voice_confirmation
            self._audit.record(
                AuditEvent(
                    source=source,
                    requested_by=AuditActor.USER,
                    path=entry.pending.path,
                    old_value=entry.pending.old_value,
                    new_value=entry.pending.new_value,
                    ok=False,
                    rolled_back=False,
                    error=reason,
                    **extras,
                )
            )
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("Reject-Audit fehlgeschlagen: %s", exc)

    def cleanup_expired(self) -> int:
        """Remove expired pending entries. Returns the number removed."""
        with self._lock:
            return self._cleanup_expired_locked()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _cleanup_expired_locked(self) -> int:
        now = time.time()
        expired = [
            pid
            for pid, entry in self._entries.items()
            if now - entry.pending.created_at > self._ttl_seconds
        ]
        for pid in expired:
            self._entries.pop(pid, None)
        return len(expired)

    def _read_old_value(self, path: str) -> Any:
        try:
            return self._writer.read_value(path)
        except Exception as exc:  # noqa: BLE001
            _LOG.warning(
                "PendingMutationStore: read_value(%s) failed: %s", path, exc
            )
            return None

    # ------------------------------------------------------------------
    # Test-Hooks
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


# Re-export for the `jarvis.core.self_mod` public API.
__all__ = ["PendingMutation", "PendingMutationStore"]


# Suppress lint for unused imports — `AllowlistViolationError`,
# `SecretAccessError`, and `PreValidateError` are propagated via the pipeline's
# "raises" contract and are referenced in the `create()` docstring for mypy
# narrowing. Plan requirement: tools forward them as tool failures.
_ = (AllowlistViolationError, SecretAccessError, PreValidateError)
