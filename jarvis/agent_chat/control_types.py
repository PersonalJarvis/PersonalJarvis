"""Validated, provider-neutral state for explicit chat controls."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CommandName = Literal[
    "help",
    "clear",
    "history",
    "plan",
    "build",
    "goal",
    "status",
    "stop",
    "continue",
    "recap",
    "find",
    "model",
    "remember",
    "message",
    "review",
    "routines",
]
GoalStatus = Literal["active", "paused", "blocked", "complete", "cleared"]


class CommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command: CommandName
    arguments: str = Field(default="", max_length=16000)
    request_id: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")
    locale: Literal["en", "de", "es"] = "en"
    attachments: list[dict] = Field(default_factory=list)


class GoalState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    objective: str = Field(min_length=1, max_length=4000)
    status: GoalStatus = "active"
    engine: str = "jarvis"
    native_pending: bool = False
    reason: str = ""
    steps: int = 0
    stalled_steps: int = 0
    evidence: list[str] = Field(default_factory=list)
    started_ms: int
    updated_ms: int


class ChatControlState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    mode: Literal["build", "plan"] = "build"
    permission_mode: str = ""
    output_language: str = ""
    previous_permission: str = ""
    plan: str = ""
    last_request: str = ""
    last_status: Literal["idle", "running", "done", "interrupted", "failed"] = "idle"
    goal: GoalState | None = None
    revision: int = 0


class GoalVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["continue", "waiting", "complete", "blocked"]
    reason: str = Field(min_length=1)
    evidence: list[str] = Field(default_factory=list)
    progress: bool = False


class CommandResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str
    command: CommandName
    status: Literal["done", "started", "failed"] = "done"
    state: ChatControlState
    data: dict = Field(default_factory=dict)
    error: str = ""


COMMANDS: tuple[dict[str, str], ...] = (
    {"name": "help", "kind": "local", "example": "/help"},
    {"name": "clear", "kind": "local", "example": "/clear"},
    {"name": "history", "kind": "local", "example": "/history"},
    {"name": "plan", "kind": "control", "example": "/plan Analyze the task"},
    {"name": "build", "kind": "control", "example": "/build"},
    {"name": "goal", "kind": "control", "example": "/goal Finish all acceptance criteria"},
    {"name": "status", "kind": "control", "example": "/status"},
    {"name": "stop", "kind": "control", "example": "/stop"},
    {"name": "continue", "kind": "control", "example": "/continue"},
    {"name": "recap", "kind": "control", "example": "/recap"},
    {"name": "find", "kind": "control", "example": "/find earlier decision"},
    {"name": "model", "kind": "local", "example": "/model"},
    {"name": "remember", "kind": "control", "example": "/remember Prefer concise reports"},
    {"name": "message", "kind": "control", "example": "/message @Agent Hello"},
    {"name": "review", "kind": "control", "example": "/review"},
    {"name": "routines", "kind": "local", "example": "/routines"},
)
