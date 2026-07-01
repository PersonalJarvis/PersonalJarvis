"""TaskSpec schema — Pydantic models for the persistent task queue.

The trigger scope is deliberately small (mandate §8.3, user-confirmed):
`after_delay`, `at_time`, `on_event`. No cron (skills have that already),
no RRULE.

ADR-0003 describes the DB schema; this module covers the in-memory and JSON
representation.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------
# Trigger
# ---------------------------------------------------------------------

class TriggerAfterDelay(BaseModel):
    """'In N seconds' — relative to `time.time_ns()` at scheduling time."""
    model_config = ConfigDict(frozen=True, extra="forbid")
    type: Literal["after_delay"] = "after_delay"
    delay_seconds: float = Field(gt=0, le=30 * 24 * 3600)   # max 30 days


class TriggerAtTime(BaseModel):
    """Absolute point in time, ISO-8601 with timezone. Local time without a
    TZ is interpreted as the system zone.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")
    type: Literal["at_time"] = "at_time"
    iso_timestamp: str = Field(min_length=10, max_length=40)


class TriggerOnEvent(BaseModel):
    """'When event X happens' — the event class name plus an optional
    filter expression (field comparison). Example:

        event_name = "MessageSent"
        filter_expr = "role == 'user'"
    """
    model_config = ConfigDict(frozen=True, extra="forbid")
    type: Literal["on_event"] = "on_event"
    event_name: str = Field(min_length=1, max_length=64,
                            pattern=r"^[A-Z][A-Za-z0-9]+$")
    filter_expr: str | None = Field(default=None, max_length=256)
    max_firings: int | None = Field(default=1, ge=1, le=1000)


class TriggerEvery(BaseModel):
    """Recurring interval — 'every N seconds' (hourly / daily / custom).

    Added 2026-06-17 for the Tasks section's recurring-schedule requirement.
    Deliberately interval-based, NOT a raw cron expression (keeps the
    'no cron' contract from ADR-0003 while still covering hourly/daily).

    - ``interval_seconds`` is the gap between runs (3600 = hourly,
      86400 = daily). Capped at one year.
    - ``start_at`` optionally anchors the first run to an absolute ISO-8601
      timestamp (e.g. 'daily at 07:00'). When omitted, the first run is one
      interval from scheduling time.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")
    type: Literal["every"] = "every"
    interval_seconds: float = Field(gt=0, le=366 * 24 * 3600)   # max 1 year
    start_at: str | None = Field(default=None, min_length=10, max_length=40)


Trigger = Annotated[
    TriggerAfterDelay | TriggerAtTime | TriggerOnEvent | TriggerEvery,
    Field(discriminator="type"),
]


TRIGGER_TYPES: tuple[str, ...] = ("after_delay", "at_time", "on_event", "every")


# ---------------------------------------------------------------------
# Action — what runs when the trigger fires
# ---------------------------------------------------------------------

class HarnessDispatchAction(BaseModel):
    """Dispatches to a harness (openclaw, computer-use, ...)."""
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["harness_dispatch"] = "harness_dispatch"
    harness: str = Field(min_length=1, max_length=64)
    prompt: str = Field(min_length=1, max_length=16_384)
    allow_computer_use: bool = False


class SpeakAction(BaseModel):
    """TTS — Jarvis says a fixed sentence (e.g. 'remind me tonight')."""
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["speak"] = "speak"
    text: str = Field(min_length=1, max_length=2048)


class ToolCallAction(BaseModel):
    """Run a single tool (e.g. 'open_app Outlook')."""
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["tool_call"] = "tool_call"
    tool_name: str = Field(min_length=1, max_length=64)
    args: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------
# Plugin grants — per-task pre-authorization for unattended runs
# ---------------------------------------------------------------------

# Permission scope a task grants an enabled plugin. Maps onto the risk-tier
# system: `read` keeps the agent to safe/monitor calls; `write`/`full`
# pre-authorize `ask`-tier actions (send mail, post tweet) so an unattended
# scheduled run does not block on a human confirmation.
PluginScope = Literal["read", "write", "full"]
PLUGIN_SCOPES: tuple[str, ...] = ("read", "write", "full")


class PluginGrant(BaseModel):
    """One enabled plugin plus the permission scope the user toggled for it."""
    model_config = ConfigDict(frozen=True, extra="forbid")
    plugin_id: str = Field(min_length=1, max_length=64)
    scope: PluginScope = "read"


class AgentAction(BaseModel):
    """An agentic brain turn — the task runs ``prompt`` and the brain decides
    how to combine the enabled plugins to reach the goal (Claude-style
    scheduled task). The toggled plugins become the turn's tool allowlist;
    each grant's ``scope`` gates what the unattended run may do.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["agent"] = "agent"
    prompt: str = Field(min_length=1, max_length=16_384)
    plugin_grants: tuple[PluginGrant, ...] = Field(default_factory=tuple)
    model_tier: Literal["fast", "deep", "auto"] = "auto"


TaskAction = Annotated[
    HarnessDispatchAction | SpeakAction | ToolCallAction | AgentAction,
    Field(discriminator="kind"),
]


ACTION_KINDS: tuple[str, ...] = ("harness_dispatch", "speak", "tool_call", "agent")


# ---------------------------------------------------------------------
# Retry policy
# ---------------------------------------------------------------------

class RetryPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    max_attempts: int = Field(default=1, ge=1, le=10)
    backoff_initial_s: float = Field(default=5.0, ge=0, le=3600)
    backoff_factor: float = Field(default=2.0, ge=1.0, le=10.0)
    retry_on_interrupt: bool = True          # startup cleanup (ADR-0003)


# ---------------------------------------------------------------------
# TaskSpec + state
# ---------------------------------------------------------------------

TaskState = Literal[
    "pending",          # never scheduled yet (e.g. created_from_api, waiting for hydrate)
    "scheduled",        # in the heap or waiting for an event
    "running",          # the TaskRunner is currently executing it
    "completed",        # finished successfully
    "failed",           # given up after max_attempts
    "cancelled",        # manual or kill-switch
    "interrupted",      # app exit while running (ADR-0003)
]


TASK_STATES: tuple[str, ...] = (
    "pending", "scheduled", "running", "completed",
    "failed", "cancelled", "interrupted",
)


class TaskSpec(BaseModel):
    """The description of a scheduled task — persisted as JSON in
    `tasks.spec_json`.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    title: str = Field(min_length=1, max_length=256)
    trigger: Trigger
    action: TaskAction
    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)
    created_at_ns: int = 0
    created_by: str = "user"                 # "user" | "skill" | "brain"
    tags: tuple[str, ...] = Field(default_factory=tuple)
    # When-Then notify: a spoken confirmation emitted after the action's
    # terminal outcome, action-agnostic (the `harness_dispatch`/CU path does not
    # self-announce, unlike `agent`). Published as AnnouncementRequested(
    # kind="subagent"), which punches through the voice hangup gate and is
    # mirrored to browser tabs — so "let me know" works post-hangup and headless.
    # Both support {field} placeholders interpolated from the triggering event
    # (e.g. "Done — opened {result_uri}."). None = stay silent (legacy behaviour).
    announce_on_success: str | None = Field(default=None, max_length=2048)
    announce_on_failure: str | None = Field(default=None, max_length=2048)
