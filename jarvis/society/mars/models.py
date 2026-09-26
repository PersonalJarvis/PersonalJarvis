"""Versioned, bounded station messages shared by storage and HTTP projections."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

WORLD_ID = "mars:ordinary"
STATION_ID = "communications-console"
CAPABILITY_ID = "communication-draft"
SCHEMA_VERSION = 1
LAYOUT_VERSION = 1

Identity = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")]
Reference = Annotated[str, Field(min_length=1, max_length=256)]


class CommandState(StrEnum):
    QUEUED = "queued"
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"
    INTERRUPTED = "interrupted"
    UNKNOWN = "unknown"


TERMINAL_STATES = frozenset({CommandState.COMPLETED, CommandState.FAILED, CommandState.CANCELED})


class StationError(Exception):
    """Public generic error code; never carries an upstream provider response."""

    def __init__(self, reason: str, status_code: int = 409) -> None:
        if re.fullmatch(r"[a-z_]{1,80}", reason) is None:
            reason = "station_operation_failed"
        super().__init__(reason)
        self.reason = reason
        self.status_code = status_code


class DispatchRejected(StationError):
    """Trusted adapter guarantees no task or side effect was accepted."""


def reject_draft_credentials(draft: str) -> None:
    """Reject credentials before any storage, session creation or execution.

    This belongs at the acceptance boundary, outside Pydantic validators:
    HTTP validation errors otherwise include the rejected input by default.
    The public reason points to in-app key settings without echoing a value.
    """
    from jarvis.memory.wiki.secret_guard import contains_secret

    if contains_secret(draft):
        raise DispatchRejected("credential_input_use_api_key_settings", 422)


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class StationCommand(ContractModel):
    """Identity of the acting agent comes from the trusted server boundary."""

    world_id: Literal["mars:ordinary"] = WORLD_ID
    station_id: Literal["communications-console"] = STATION_ID
    capability_id: Literal["communication-draft"] = CAPABILITY_ID
    schema_version: Literal[1] = SCHEMA_VERSION
    layout_version: Literal[1] = LAYOUT_VERSION
    request_id: Identity
    draft: str = Field(min_length=1, max_length=4000)

    @field_validator("schema_version", "layout_version", mode="before")
    @classmethod
    def exact_version(cls, value: object) -> object:
        if type(value) is not int or value != 1:
            raise ValueError("unsupported station contract version")
        return value

    @field_validator("draft")
    @classmethod
    def nonblank_draft(cls, value: str) -> str:
        value = value.strip()
        if not value or "\x00" in value:
            raise ValueError("draft must contain text without null characters")
        return value


class CommandRecord(ContractModel):
    command_id: Identity
    world_id: Literal["mars:ordinary"] = WORLD_ID
    station_id: Literal["communications-console"] = STATION_ID
    capability_id: Literal["communication-draft"] = CAPABILITY_ID
    agent_id: Identity
    request_id: Identity
    trace_id: Identity
    state: CommandState
    # Private local input is retained for dispatch, never sent in world events/snapshots.
    draft: str = Field(exclude=True)
    created_ms: int
    updated_ms: int
    fence: int = 0
    task_ref: Reference | None = None
    result_ref: Reference | None = None
    reason: str = ""
    cancel_requested: bool = False
    cancel_dispatched: bool = Field(default=False, exclude=True)


class StationLease(ContractModel):
    station_id: Literal["communications-console"] = STATION_ID
    command_id: Identity
    agent_id: Identity
    fence: int
    expires_ms: int


class ExecutionReceipt(ContractModel):
    """Trusted adapter evidence. A missing result cannot count as completion."""

    state: CommandState
    task_ref: Reference | None = None
    result_ref: Reference | None = None
    # Only cancellation receipts set this. A proven no-op may be retried once
    # prerequisites recover; an attempted or uncertain stop must be reconciled.
    cancel_attempt: Literal["not_attempted", "attempted", "unknown"] | None = None

    @model_validator(mode="after")
    def valid_outcome(self) -> ExecutionReceipt:
        if self.state is CommandState.QUEUED:
            raise ValueError("executor cannot return a station queue state")
        if self.state in {CommandState.ACTIVE, CommandState.COMPLETED} and not self.task_ref:
            raise ValueError("accepted execution requires a durable task reference")
        if self.state is CommandState.COMPLETED and not self.result_ref:
            raise ValueError("completion requires a durable result reference")
        return self


class StationEvent(ContractModel):
    seq: int
    command_id: str
    trace_id: str
    agent_id: str
    state: CommandState
    fence: int
    task_ref: str | None = None
    result_ref: str | None = None
    reason: str
    cancel_requested: bool
    ts_ms: int


class StationSnapshot(ContractModel):
    world_id: Literal["mars:ordinary"] = WORLD_ID
    schema_version: Literal[1] = SCHEMA_VERSION
    layout_version: Literal[1] = LAYOUT_VERSION
    seq: int
    commands: tuple[CommandRecord, ...]
    lease: StationLease | None


class StationEventBatch(ContractModel):
    world_id: Literal["mars:ordinary"] = WORLD_ID
    schema_version: Literal[1] = SCHEMA_VERSION
    layout_version: Literal[1] = LAYOUT_VERSION
    events: tuple[StationEvent, ...]
    next_cursor: int
    latest_seq: int
    has_more: bool
    resync_required: bool
