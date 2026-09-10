"""TaskSpec schema — Pydantic models for the persistent task queue.

Triggers support delays, absolute timestamps, events, elapsed intervals and
explicit timezone-aware calendar/cron rules and typed input sources.

ADR-0003 describes the DB schema; this module covers the in-memory and JSON
representation.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from .source_schema import SourceSettings

# ---------------------------------------------------------------------
# Trigger
# ---------------------------------------------------------------------


class TriggerAfterDelay(BaseModel):
    """'In N seconds' — relative to `time.time_ns()` at scheduling time."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    type: Literal["after_delay"] = "after_delay"
    delay_seconds: float = Field(gt=0, le=30 * 24 * 3600)  # max 30 days


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
    event_name: str = Field(min_length=1, max_length=64, pattern=r"^[A-Z][A-Za-z0-9]+$")
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
    interval_seconds: float = Field(gt=0, le=366 * 24 * 3600)  # max 1 year
    start_at: str | None = Field(default=None, min_length=10, max_length=40)


HookScalar = StrictStr | StrictBool | StrictInt | StrictFloat | None


class HookOptions(BaseModel):
    """JSON field equality filters and a lifetime delivery limit."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)
    conditions: dict[str, HookScalar] = Field(default_factory=dict, max_length=20)
    max_firings: int | None = Field(default=None, ge=1, le=1000)
    cooldown_seconds: float = Field(default=0, ge=0, le=86400)

    @field_validator("conditions")
    @classmethod
    def valid_paths(cls, value: dict[str, HookScalar]) -> dict[str, HookScalar]:
        import re

        for key in value:
            if len(key) > 160 or not re.fullmatch(r"[A-Za-z_][\w-]*(?:\.[A-Za-z_][\w-]*)*", key):
                raise ValueError("Conditions use dot-separated JSON object field names")
        return value


class TriggerWebhook(HookOptions):
    """Authenticated JSON POST to this routine's webhook endpoint."""

    type: Literal["webhook"] = "webhook"
    provider: Literal["generic", "github", "linear", "gmail", "slack", "stripe"] = "generic"
    oidc_audience: str = Field(default="", max_length=2000)
    service_account: str = Field(default="", max_length=320)

    @model_validator(mode="after")
    def provider_settings(self) -> TriggerWebhook:
        if (
            self.provider == "gmail"
            and self.service_account
            and not self.service_account.endswith(".gserviceaccount.com")
        ):
            raise ValueError("Use the Pub/Sub service account email, not the Gmail mailbox address")
        if self.provider != "gmail" and (self.oidc_audience or self.service_account):
            raise ValueError("OIDC settings belong to Gmail Pub/Sub triggers")
        return self


class TriggerEventHook(HookOptions):
    """An explicitly named event emitted by an integration or the events API."""

    type: Literal["event_hook"] = "event_hook"
    event_name: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z][A-Za-z0-9_.:-]*$")


class TriggerSource(HookOptions):
    type: Literal["source"] = "source"
    source: SourceSettings


class TriggerCron(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    type: Literal["cron"] = "cron"
    expression: str = Field(min_length=9, max_length=128)
    timezone: str = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def valid_schedule(self) -> TriggerCron:
        from .cron_schedule import validate_cron

        validate_cron(self.expression, self.timezone)
        return self


class TriggerCalendar(BaseModel):
    """Recurring wall-clock time pinned to a user's explicit IANA timezone."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    type: Literal["calendar"] = "calendar"
    timezone: str = Field(min_length=1, max_length=100)
    local_time: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    weekdays: tuple[Annotated[int, Field(ge=0, le=6)], ...] = ()
    month_days: tuple[Annotated[int, Field(ge=1, le=31)], ...] = ()
    months: tuple[Annotated[int, Field(ge=1, le=12)], ...] = ()
    start_date: str | None = None

    @field_validator("timezone")
    @classmethod
    def valid_zone(cls, value: str) -> str:
        from .calendar import calendar_zone

        calendar_zone(value)
        return value

    @field_validator("start_date")
    @classmethod
    def valid_date(cls, value: str | None) -> str | None:
        from datetime import date

        if value is not None:
            return date.fromisoformat(value).isoformat()
        return value

    @model_validator(mode="after")
    def possible_month_day(self) -> TriggerCalendar:
        from calendar import monthrange

        if (
            self.months
            and self.month_days
            and not any(
                day <= monthrange(2000, month)[1]
                for month in self.months
                for day in self.month_days
            )
        ):
            raise ValueError("Calendar rule has no matching month/day")
        return self


Trigger = Annotated[
    TriggerAfterDelay
    | TriggerAtTime
    | TriggerOnEvent
    | TriggerEvery
    | TriggerCalendar
    | TriggerWebhook
    | TriggerEventHook
    | TriggerSource
    | TriggerCron,
    Field(discriminator="type"),
]


TRIGGER_TYPES: tuple[str, ...] = (
    "after_delay",
    "at_time",
    "on_event",
    "every",
    "calendar",
    "webhook",
    "event_hook",
    "source",
    "cron",
)


# ---------------------------------------------------------------------
# Action — what runs when the trigger fires
# ---------------------------------------------------------------------


class HarnessDispatchAction(BaseModel):
    """Dispatches to a harness (jarvis_agent, computer-use, ...)."""

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


class WorkflowAction(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["workflow"] = "workflow"
    workflow_id: UUID


TaskAction = Annotated[
    HarnessDispatchAction | SpeakAction | ToolCallAction | AgentAction | WorkflowAction,
    Field(discriminator="kind"),
]


ACTION_KINDS: tuple[str, ...] = ("harness_dispatch", "speak", "tool_call", "agent", "workflow")


# ---------------------------------------------------------------------
# Retry policy
# ---------------------------------------------------------------------


class RetryPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    max_attempts: int = Field(default=1, ge=1, le=10)
    backoff_initial_s: float = Field(default=5.0, ge=0, le=3600)
    backoff_factor: float = Field(default=2.0, ge=1.0, le=10.0)
    retry_on_interrupt: bool = True  # startup cleanup (ADR-0003)


# ---------------------------------------------------------------------
# TaskSpec + state
# ---------------------------------------------------------------------

TaskState = Literal[
    "pending",  # never scheduled yet (e.g. created_from_api, waiting for hydrate)
    "scheduled",  # in the heap or waiting for an event
    "paused",  # recurring/on_event task the user switched off (resumable)
    "running",  # the TaskRunner is currently executing it
    "completed",  # finished successfully
    "failed",  # given up after max_attempts
    "cancelled",  # manual or kill-switch
    "interrupted",  # app exit while running (ADR-0003)
]


TASK_STATES: tuple[str, ...] = (
    "pending",
    "scheduled",
    "paused",
    "running",
    "completed",
    "failed",
    "cancelled",
    "interrupted",
)

#: States a task never leaves on its own (hard-delete is allowed here).
TERMINAL_STATES: tuple[str, ...] = ("completed", "failed", "cancelled", "interrupted")
#: Trigger types that can be paused/resumed — the recurring ones.
PAUSABLE_TRIGGER_TYPES: tuple[str, ...] = (
    "every",
    "calendar",
    "on_event",
    "webhook",
    "event_hook",
    "source",
    "cron",
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
    created_by: str = "user"  # "user" | "skill" | "brain"
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
