"""TaskSpec-Schema — Pydantic-Modelle fuer die persistente Task-Queue.

Trigger-Scope ist bewusst klein (Mandat §8.3, User-Bestaetigung):
`after_delay`, `at_time`, `on_event`. Kein Cron (das haben Skills),
keine RRULE.

ADR-0003 beschreibt das DB-Schema, dieses Modul die In-Memory- und
JSON-Repraesentation.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------
# Trigger
# ---------------------------------------------------------------------

class TriggerAfterDelay(BaseModel):
    """'In N Sekunden' — relativ zu `time.time_ns()` bei Scheduling."""
    model_config = ConfigDict(frozen=True, extra="forbid")
    type: Literal["after_delay"] = "after_delay"
    delay_seconds: float = Field(gt=0, le=30 * 24 * 3600)   # max 30 Tage


class TriggerAtTime(BaseModel):
    """Absoluter Zeitpunkt, ISO-8601 mit Zeitzone. Local-Time ohne TZ wird
    als System-Zone interpretiert.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")
    type: Literal["at_time"] = "at_time"
    iso_timestamp: str = Field(min_length=10, max_length=40)


class TriggerOnEvent(BaseModel):
    """'Wenn Event X passiert' — Event-Klassen-Name plus optionaler
    Filter-Ausdruck (Feldvergleich). Beispiel:

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
# Action — was beim Trigger ausgefuehrt wird
# ---------------------------------------------------------------------

class HarnessDispatchAction(BaseModel):
    """Dispatcht an einen Harness (openclaw, computer-use, ...)."""
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["harness_dispatch"] = "harness_dispatch"
    harness: str = Field(min_length=1, max_length=64)
    prompt: str = Field(min_length=1, max_length=16_384)
    allow_computer_use: bool = False


class SpeakAction(BaseModel):
    """TTS — Jarvis sagt einen fixen Satz (z.B. 'erinner mich heute Abend')."""
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["speak"] = "speak"
    text: str = Field(min_length=1, max_length=2048)


class ToolCallAction(BaseModel):
    """Einzelnen Tool ausfuehren (z.B. 'open_app Outlook')."""
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
# Retry-Policy
# ---------------------------------------------------------------------

class RetryPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    max_attempts: int = Field(default=1, ge=1, le=10)
    backoff_initial_s: float = Field(default=5.0, ge=0, le=3600)
    backoff_factor: float = Field(default=2.0, ge=1.0, le=10.0)
    retry_on_interrupt: bool = True          # Startup-Cleanup (ADR-0003)


# ---------------------------------------------------------------------
# TaskSpec + State
# ---------------------------------------------------------------------

TaskState = Literal[
    "pending",          # noch nie geschedult (z.B. created_from_api, wartet auf hydrate)
    "scheduled",        # im Heap oder wartet auf Event
    "running",          # TaskRunner fuehrt gerade aus
    "completed",        # Erfolgreich beendet
    "failed",           # Nach max_attempts aufgegeben
    "cancelled",        # Manuell oder Kill-Switch
    "interrupted",      # App-Exit waehrend running (ADR-0003)
]


TASK_STATES: tuple[str, ...] = (
    "pending", "scheduled", "running", "completed",
    "failed", "cancelled", "interrupted",
)


class TaskSpec(BaseModel):
    """Die Beschreibung eines geschedulten Tasks — persistiert als JSON in
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
