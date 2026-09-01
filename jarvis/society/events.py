"""The society's typed envelope and every enum that crosses layers.

These names cross Python → SQLite CHECK constraints → Pydantic → TypeScript
(``src/lib/societyApi.ts``) → UI, so they are pinned against each other by
``tests/unit/society/test_society_enum_parity.py`` (AP-4). Change a member
here, then follow the parity test to the other three spellings.

The message protocol (MASTERPLAN §2.1/§2.2): a compact typed event whose
payload MAY carry human-readable ``text``. Chat surfaces render the text; the
scheduler and the world act on the type.
"""

from __future__ import annotations

import time
from enum import StrEnum
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field

from jarvis.missions.ids import uuid7_str

__all__ = [
    "AgentState",
    "ApprovalState",
    "Checkpoint",
    "GrantMode",
    "KnowledgeOrigin",
    "KnowledgeScope",
    "MsgType",
    "PermissionCeiling",
    "RoomState",
    "RunState",
    "SocietyEnvelope",
    "Tier",
    "now_ms",
]


def now_ms() -> int:
    return int(time.time() * 1000)


class MsgType(StrEnum):
    """The eleven coordination types plus the two room brackets."""

    ASSIGN = "ASSIGN"
    CLAIM = "CLAIM"
    RESULT = "RESULT"
    QUERY = "QUERY"
    ANSWER = "ANSWER"
    HOLD = "HOLD"
    RELEASE = "RELEASE"
    PROPOSE = "PROPOSE"
    VETO = "VETO"
    DIGEST = "DIGEST"
    SAY = "SAY"
    ROOM_OPEN = "ROOM_OPEN"
    ROOM_SETTLE = "ROOM_SETTLE"


class Tier(StrEnum):
    LEAD = "lead"
    ORCHESTRATOR = "orchestrator"
    SPECIALIST = "specialist"


class AgentState(StrEnum):
    """Persisted lifecycle state of a roster row."""

    ACTIVE = "active"
    PAUSED = "paused"
    ARCHIVED = "archived"


class RunState(StrEnum):
    """Derived from the event log for rows and badges; never stored."""

    IDLE = "idle"
    WORKING = "working"
    WAITING = "waiting"
    PAUSED = "paused"


class Checkpoint(StrEnum):
    """The semantic place in the world; the client owns the pixels (§2.7)."""

    DESK = "desk"
    MEETING = "meeting"
    ARCHIVE = "archive"
    GATE = "gate"
    IDLE = "idle"


class PermissionCeiling(StrEnum):
    """The unattended ceiling; ``block`` never appears — block is block."""

    SAFE = "safe"
    MONITOR = "monitor"
    ASK = "ask"


class GrantMode(StrEnum):
    ALL = "all"
    ALLOWLIST = "allowlist"


class KnowledgeScope(StrEnum):
    SHARED = "shared"
    OWN = "own"


class KnowledgeOrigin(StrEnum):
    USER = "user"
    TOOL = "tool"
    WEB = "web"
    AGENT = "agent"


class RoomState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SETTLED = "settled"
    FAILED = "failed"


class ApprovalState(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"
    BLOCKED = "blocked"


#: Sender id of envelopes the trusted scheduler writes on its own behalf.
SCHEDULER_ACTOR: Final[str] = "scheduler"
#: Sender id of envelopes the person writes (typed ``@name`` in a chat, REST).
USER_ACTOR: Final[str] = "user"


class SocietyEnvelope(BaseModel):
    """One row of the append-only board.

    ``seq`` is server-assigned by the store (``None`` until persisted).
    ``to_agent`` ``None`` means broadcast. ``payload`` is compact JSON; for
    ``RESULT`` the scheduler validates the handoff record (agent-definition
    §4.3) before it accepts the event.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str = Field(default_factory=uuid7_str)
    seq: int | None = None
    msg_type: MsgType
    from_agent: str
    to_agent: str | None = None
    trace_id: str
    parent_event_id: str | None = None
    ts_ms: int = Field(default_factory=now_ms)
    cost_usd: float = 0.0
    payload: dict[str, Any] = Field(default_factory=dict)

    @property
    def text(self) -> str:
        value = self.payload.get("text")
        return value if isinstance(value, str) else ""
