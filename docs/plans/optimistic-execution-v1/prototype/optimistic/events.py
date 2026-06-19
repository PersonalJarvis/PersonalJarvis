"""Shared frozen event vocabulary — the single wire contract for the prototype.

This is the ONLY module every other component depends on. Mirrors
`jarvis/core/events.py`: every event is a `frozen=True` dataclass carrying a
`trace_id` (correlation across the Talker -> Bus -> Worker -> Oops chain) and a
`timestamp_ns` (monotonic, for latency spans). Immutability is what makes the
event stream safe to replay and safe to fan out to many subscribers.

Two enums double as the "wire-format vocabulary". In the production system these
would be the single source of truth behind the five-layer enum pattern
(`docs/anti-drift-three-layer.md`); here the Python enum IS the source.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class RouteKind(StrEnum):
    """The Talker's routing decision for one utterance."""

    SMALLTALK = "smalltalk"      # answered directly, no tool, never wakes the worker
    DUMB_TOOL = "dumb_tool"      # local script, fired in-process in milliseconds
    SMART_TOOL = "smart_tool"    # complex MCP call, delegated to the background worker


class CorrectionReason(StrEnum):
    """Why a background mission could not complete — drives the Oops phrasing.

    `StrEnum` so the value serialises cleanly and compares to plain strings,
    exactly like the production wire enums.
    """

    MISSING_INFO = "missing_info"      # recoverable: ask the user for the missing piece
    AUTH_REQUIRED = "auth_required"    # recoverable: a credential/login is needed
    NETWORK_ERROR = "network_error"    # retryable: transient, silent retry then maybe ask
    FATAL = "fatal"                    # not recoverable: audit + brief apology


@dataclass(frozen=True, slots=True, kw_only=True)
class Event:
    """Base event. `kw_only=True` sidesteps the dataclass-inheritance trap where
    a defaulted base field would forbid non-defaulted subclass fields."""

    trace_id: uuid.UUID = field(default_factory=uuid.uuid4)
    timestamp_ns: int = field(default_factory=time.monotonic_ns)


@dataclass(frozen=True, slots=True, kw_only=True)
class UserUtterance(Event):
    """The user said something. Start of a trace."""

    text: str


@dataclass(frozen=True, slots=True, kw_only=True)
class AckEmitted(Event):
    """The Talker's optimistic acknowledgement ("Geht klar"). MUST be emitted
    before any worker dispatch returns (AD-OE1)."""

    text: str


@dataclass(frozen=True, slots=True, kw_only=True)
class MissionSpawn(Event):
    """The "silent context package" handed to the Heavy-Duty Worker: the command
    plus the conversation context. Publishing this never blocks the Talker (AD-OE2)."""

    command: str
    context: dict[str, Any] = field(default_factory=dict)
    tool_name: str | None = None
    mission_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])


@dataclass(frozen=True, slots=True, kw_only=True)
class WorkerStarted(Event):
    """The background worker picked up a mission and began async work."""

    mission_id: str
    tool_name: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class WorkerCompleted(Event):
    """The background worker finished a mission successfully."""

    mission_id: str
    result: str


@dataclass(frozen=True, slots=True, kw_only=True)
class WorkerCorrectionNeeded(Event):
    """The "Oops" event: an invisible signal that a mission cannot finish without
    user input or further handling. Fed into the Talker context, never spoken
    immediately (AD-OE5)."""

    mission_id: str
    reason: CorrectionReason
    detail: str
    command: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class DumbToolFired(Event):
    """A local 'dumb' tool ran in-process. Recorded for the flight log; it must
    never have woken the worker (AD-OE3)."""

    action: str
