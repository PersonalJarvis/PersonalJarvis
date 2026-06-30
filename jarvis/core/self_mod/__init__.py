"""Self-modification pipeline (Phase 7+).

Design reference: `docs/self_mod.md`.

Phase 7.1 delivers: a hardcoded allowlist (`SelfModRegistry`) and an
append-only audit log (`SelfModAudit`). Phase 7.2 adds the atomic writer,
Phase 7.3 the brain tools.
"""
from __future__ import annotations

from .audit import SelfModAudit
from .errors import (
    AllowlistViolationError,
    BackupError,
    PreValidateError,
    ProviderSwitchLockedError,
    ReloadError,
    RollbackError,
    SecretAccessError,
    SelfModError,
    TypeMismatchError,
)
from .pending import PendingMutation, PendingMutationStore
from .provider_lock import PROVIDER_LOCK_PATHS, is_provider_lock_path
from .registry import FORBIDDEN_PATTERNS, SelfModRegistry
from .schema import (
    AuditActor,
    AuditEvent,
    AuditSource,
    BackupRef,
    MutableSpec,
    MutationRequest,
    MutationResult,
    SelfModRiskTier,
)
from .writer import AtomicConfigWriter

__all__ = [
    "FORBIDDEN_PATTERNS",
    "PROVIDER_LOCK_PATHS",
    "AllowlistViolationError",
    "AtomicConfigWriter",
    "AuditActor",
    "AuditEvent",
    "AuditSource",
    "BackupError",
    "BackupRef",
    "MutableSpec",
    "MutationRequest",
    "MutationResult",
    "PendingMutation",
    "PendingMutationStore",
    "PreValidateError",
    "ProviderSwitchLockedError",
    "ReloadError",
    "RollbackError",
    "SecretAccessError",
    "SelfModAudit",
    "SelfModError",
    "SelfModRegistry",
    "SelfModRiskTier",
    "TypeMismatchError",
    "is_provider_lock_path",
]
