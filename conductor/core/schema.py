"""Pydantic schema — jobs, runs, schedules.

Three central design decisions:

1. **Jobs as a discriminated union of JobSpecs.** A new job type is
   a 3-file change (spec model, handler, YAML example). No plugin
   system needed for the MVP.

2. **Schedule is also a discriminated union.** Cron, interval,
   manual, webhook — that covers everything from a user click to an
   external push trigger.

3. **Runs have steps**, even though currently every job has only one
   step. That makes us v0.2-ready for multi-step agents (an LLM
   tool-use loop where every tool call is its own step).
"""
from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------
# Schedules
# ---------------------------------------------------------------------

class CronSchedule(BaseModel):
    """Cron expression with 5 fields (standard syntax, via croniter)."""
    model_config = ConfigDict(frozen=True, extra="forbid")
    type: Literal["cron"] = "cron"
    expression: str = Field(min_length=9, max_length=128,
                             description="e.g. '0 9 * * *' for 9:00 daily")
    timezone: str = Field(default="local")


class IntervalSchedule(BaseModel):
    """Repeats every N seconds — simpler than cron."""
    model_config = ConfigDict(frozen=True, extra="forbid")
    type: Literal["interval"] = "interval"
    seconds: int = Field(ge=10, le=30 * 24 * 3600,
                          description="Minimum 10s (rate-limit protection), "
                          "max 30 days.")


class ManualSchedule(BaseModel):
    """Only on button press / CLI run."""
    model_config = ConfigDict(frozen=True, extra="forbid")
    type: Literal["manual"] = "manual"


class WebhookSchedule(BaseModel):
    """Trigger via HTTP POST to ``/api/conductor/hooks/<token>``.

    The ``token`` is generated on creation so the webhook URL is
    unguessable. Runs get the POST body as ``input``.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")
    type: Literal["webhook"] = "webhook"
    token: str = Field(min_length=16, max_length=64,
                        description="URL-safe hex token, generated on "
                        "creation.")


Schedule = Annotated[
    CronSchedule | IntervalSchedule | ManualSchedule | WebhookSchedule,
    Field(discriminator="type"),
]


# ---------------------------------------------------------------------
# Job specs — what runs when triggered
# ---------------------------------------------------------------------

class ShellJobSpec(BaseModel):
    """Shell command with args, timeout, working directory."""
    model_config = ConfigDict(frozen=True, extra="forbid")
    type: Literal["shell"] = "shell"
    command: str = Field(min_length=1, max_length=4096)
    cwd: str = Field(default="", max_length=512)
    env: dict[str, str] = Field(default_factory=dict)
    timeout_s: float = Field(default=120.0, ge=1.0, le=3600.0)


class HttpJobSpec(BaseModel):
    """HTTP request — GET/POST/PUT/DELETE.

    The response body (string, max 64 KB) is persisted as the run output.
    ``expect_status`` defines the success criterion; default ``2xx``.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")
    type: Literal["http"] = "http"
    method: Literal["GET", "POST", "PUT", "DELETE", "PATCH"] = "GET"
    url: str = Field(min_length=8, max_length=2048)
    headers: dict[str, str] = Field(default_factory=dict)
    body: str | None = None
    timeout_s: float = Field(default=30.0, ge=1.0, le=300.0)
    expect_status: str = Field(default="2xx",
                                description="'2xx', '200', '3xx', or an exact code.")


class AgentJobSpec(BaseModel):
    """LLM agent call — single-turn in v0.1, tool-use in v0.2.

    Providers:

    - ``gemini``    — **Google AI Studio default.** Uses the ``google-genai``
      SDK, key from ``GEMINI_API_KEY`` or ``GOOGLE_AIStudio_API_KEY``.
      Default model ``gemini-3.1-pro`` (frontier).
    - ``anthropic`` — for users with an OpenClaw subscription. Shells out
      to the local ``claude`` CLI (OAuth session from the Max-plan login),
      needs no API key. Models: ``sonnet``, ``opus``, ``haiku``, or the
      full name (``claude-sonnet-4-6``).
    - ``anthropic`` — direct API call via the ``ANTHROPIC_API_KEY`` env var.
      For users without a subscription.
    - ``openai``    — ``OPENAI_API_KEY``.
    - ``ollama``    — local, no key. Default host ``http://localhost:11434``.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")
    type: Literal["agent"] = "agent"
    provider: Literal[
        "gemini", "anthropic", "openai", "ollama"
    ] = "gemini"
    model: str = Field(
        default="gemini-3.1-pro", min_length=1, max_length=128,
        description="Alias ('sonnet', 'opus') or full name "
        "('gemini-3.1-pro', 'claude-sonnet-4-6', 'gpt-4o', 'llama3.1'). "
        "Default: gemini-3.1-pro (matching the default provider 'gemini').",
    )
    system_prompt: str = Field(default="", max_length=8192)
    user_prompt: str = Field(min_length=1, max_length=32_768)
    max_output_tokens: int = Field(default=1024, ge=64, le=16_384)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)


JobSpec = Annotated[
    ShellJobSpec | HttpJobSpec | AgentJobSpec,
    Field(discriminator="type"),
]


# ---------------------------------------------------------------------
# Job + Run
# ---------------------------------------------------------------------

RunState = Literal["pending", "running", "completed", "failed", "cancelled"]


class Job(BaseModel):
    """Blueprint of a repeatable job — persisted in the ``jobs`` table.

    The ``spec`` is the complete job payload (type + args) and is
    serialized as JSON. So is ``schedule``.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=1024)
    spec: JobSpec
    schedule: Schedule
    enabled: bool = True
    created_at_ns: int = 0
    tags: tuple[str, ...] = Field(default_factory=tuple)


class Run(BaseModel):
    """One concrete run of a job."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    job_id: UUID
    state: RunState = "pending"
    trigger: str = "manual"              # "manual" | "cron" | "interval" | "webhook"
    started_at_ns: int = 0
    finished_at_ns: int = 0
    exit_code: int | None = None
    output: str = ""
    error: str | None = None
    input_json: str = "{}"
    metrics_json: str = "{}"             # tokens, cost_usd, http_status, bytes etc.


class RunStep(BaseModel):
    """Sub-step of a run — for v0.2 multi-step agents."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: UUID
    seq: int
    kind: str                             # "tool_call" | "llm_message" | "log"
    label: str = ""
    started_at_ns: int = 0
    finished_at_ns: int = 0
    success: bool = False
    payload_json: str = "{}"
