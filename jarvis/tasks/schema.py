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


Trigger = Annotated[
    TriggerAfterDelay | TriggerAtTime | TriggerOnEvent,
    Field(discriminator="type"),
]


TRIGGER_TYPES: tuple[str, ...] = ("after_delay", "at_time", "on_event")


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


TaskAction = Annotated[
    HarnessDispatchAction | SpeakAction | ToolCallAction,
    Field(discriminator="kind"),
]


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
