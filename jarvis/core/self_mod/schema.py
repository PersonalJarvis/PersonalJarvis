"""Pydantic models for the self-mod pipeline (Phase 7.1+).

Plan reference: §7.1 — `MutableSpec`, `MutationRequest`, `MutationResult`,
`AuditEvent`. Phase 7.1 only uses `MutableSpec` and `AuditEvent` actively;
`MutationRequest`/`MutationResult` are preparatory work for Phase 7.2 (Atomic
Writer) and Phase 7.3 (Brain Tools).
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

# Plan-§AD-4/§AD-10: Self-Mod only recognizes SAFE/ASK. MONITOR/BLOCK from the
# general RiskTier vocabulary belong to the tool-execution pipeline,
# not to setting mutations.
SelfModRiskTier = Literal["safe", "ask"]


class AuditSource(StrEnum):
    """Input channel from which the mutation originates (Plan-§7.1)."""

    VOICE = "voice"
    CHAT = "chat"
    UI = "ui"


class AuditActor(StrEnum):
    """Logical initiator in the sense of the plan field `requested_by`.

    Wave-4 migration: ``SUB_JARVIS`` renamed to ``OPENCLAW`` (see
    docs/openclaw-bridge.md §11). Alias is retained for backwards compatibility
    of historical audit entries.
    """

    HAUPTJARVIS = "hauptjarvis"
    OPENCLAW = "openclaw"
    SUB_JARVIS = "sub-jarvis"  # legacy, ehemalige Sub-Jarvis-Tier-Eintraege
    USER = "user"
    SYSTEM = "system"


class MutableSpec(BaseModel):
    """Specification of a mutable config path.

    The plan field `pydantic_model` (a validation class) is represented here
    as a string name. Rationale in `ASSUMPTIONS.md` (A-7):
    class objects make the model harder to serialize (audit UI, JSON export)
    and lead to an import cycle `schema → config → schema`.
    Phase 7.2 resolves the name via `getattr(jarvis.core.config, name)`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str = Field(min_length=1, description="Dotted-Pfad in jarvis.toml")
    pydantic_model_name: str = Field(min_length=1)
    field_name: str = Field(min_length=1)
    risk_tier: SelfModRiskTier = "ask"
    needs_restart: bool = False
    description: str = Field(min_length=1)
    sensitive: bool = False  # Phase 7.4: Wert wird im TTS-Echo nie gesprochen


class MutationRequest(BaseModel):
    """Pending mutation. Created by Hauptjarvis in Phase 7.3."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str
    new_value: Any
    actor: AuditActor = AuditActor.USER
    source: AuditSource = AuditSource.VOICE
    reason: str | None = None
    correlation_id: UUID = Field(default_factory=uuid4)


class MutationResult(BaseModel):
    """Result of a mutation. Phase 7.2 populates the model."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    request: MutationRequest
    ok: bool
    old_value: Any = None
    new_value: Any = None
    error_kind: str | None = None
    error_message: str | None = None
    rolled_back: bool = False
    backup_path: str | None = None


class BackupRef(BaseModel):
    """Read-only reference to a backup file.

    Phase 7.2: returned by the `AtomicConfigWriter.list_backups()` endpoint,
    rendered by the audit UI in Phase 7.6. Path is a string (not `Path`)
    so that the model is trivially JSON-serializable.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    filename: str
    path: str
    timestamp: datetime
    size_bytes: int
    age_seconds: float


class AuditEvent(BaseModel):
    """Plan-§7.1 audit format.

    `extra="allow"` lets Phase 7.5 extend the schema additively
    (e.g. `type=skill_authored`) without invalidating old logs.
    """

    model_config = ConfigDict(extra="allow")

    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))
    audit_id: UUID = Field(default_factory=uuid4)
    source: AuditSource
    requested_by: AuditActor
    path: str
    old_value: Any = None
    new_value: Any = None
    ok: bool
    rolled_back: bool = False
    error: str | None = None

    def to_jsonline(self) -> str:
        """Serializes to a single JSON line (UTF-8, no trailing newline).

        - Datetime as ISO-8601 with `Z` suffix for UTC (plan example format).
        - UUID, Enum: stringified via `model_dump(mode="json")`.
        - Field order matches the Plan-§7.1 example.
        """
        data = self.model_dump(mode="json")
        ts = data.get("ts")
        if isinstance(ts, str) and ts.endswith("+00:00"):
            data["ts"] = ts[:-6] + "Z"
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"))
