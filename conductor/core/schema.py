"""Pydantic-Schema — Jobs, Runs, Schedules.

Drei zentrale Design-Entscheidungen:

1. **Jobs als Discriminated-Union von JobSpecs.** Neue Job-Types sind
   eine 3-Datei-Aenderung (Spec-Model, Handler, YAML-Beispiel). Kein
   Plugin-System noetig fuer MVP.

2. **Schedule ebenfalls als Discriminated-Union.** Cron, Interval,
   Manual, Webhook — das deckt alles vom User-Klick bis zum externen
   Push-Trigger ab.

3. **Runs haben Steps**, auch wenn aktuell jeder Job nur einen Step hat.
   Das macht uns v0.2-ready fuer Multi-Step-Agenten (LLM-Tool-Use-Loop,
   bei dem jeder Tool-Call eigener Step ist).
"""
from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------
# Schedules
# ---------------------------------------------------------------------

class CronSchedule(BaseModel):
    """Cron-Expression mit 5 Feldern (Standard-Syntax, via croniter)."""
    model_config = ConfigDict(frozen=True, extra="forbid")
    type: Literal["cron"] = "cron"
    expression: str = Field(min_length=9, max_length=128,
                             description="z.B. '0 9 * * *' fuer 9:00 taeglich")
    timezone: str = Field(default="local")


class IntervalSchedule(BaseModel):
    """Wiederholung alle N Sekunden — simpler als Cron."""
    model_config = ConfigDict(frozen=True, extra="forbid")
    type: Literal["interval"] = "interval"
    seconds: int = Field(ge=10, le=30 * 24 * 3600,
                          description="Minimum 10s (Rate-Limit-Schutz), "
                          "Max 30 Tage.")


class ManualSchedule(BaseModel):
    """Nur auf Knopfdruck / CLI-Run."""
    model_config = ConfigDict(frozen=True, extra="forbid")
    type: Literal["manual"] = "manual"


class WebhookSchedule(BaseModel):
    """Trigger via HTTP-POST auf ``/api/conductor/hooks/<token>``.

    Der ``token`` wird beim Anlegen generiert, damit die Webhook-URL
    unguessable ist. Runs bekommen den POST-Body als ``input``.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")
    type: Literal["webhook"] = "webhook"
    token: str = Field(min_length=16, max_length=64,
                        description="URL-safe Hex-Token, wird beim Anlegen "
                        "generiert.")


Schedule = Annotated[
    CronSchedule | IntervalSchedule | ManualSchedule | WebhookSchedule,
    Field(discriminator="type"),
]


# ---------------------------------------------------------------------
# Job-Specs — was beim Trigger ausgefuehrt wird
# ---------------------------------------------------------------------

class ShellJobSpec(BaseModel):
    """Shell-Command mit Args, Timeout, Working-Directory."""
    model_config = ConfigDict(frozen=True, extra="forbid")
    type: Literal["shell"] = "shell"
    command: str = Field(min_length=1, max_length=4096)
    cwd: str = Field(default="", max_length=512)
    env: dict[str, str] = Field(default_factory=dict)
    timeout_s: float = Field(default=120.0, ge=1.0, le=3600.0)


class HttpJobSpec(BaseModel):
    """HTTP-Request — GET/POST/PUT/DELETE.

    Response-Body (string, max 64 KB) wird als Run-Output persistiert.
    ``expect_status`` definiert Success-Kriterium; default ``2xx``.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")
    type: Literal["http"] = "http"
    method: Literal["GET", "POST", "PUT", "DELETE", "PATCH"] = "GET"
    url: str = Field(min_length=8, max_length=2048)
    headers: dict[str, str] = Field(default_factory=dict)
    body: str | None = None
    timeout_s: float = Field(default=30.0, ge=1.0, le=300.0)
    expect_status: str = Field(default="2xx",
                                description="'2xx', '200', '3xx' oder exakter Code.")


class AgentJobSpec(BaseModel):
    """LLM-Agent-Call — single-turn in v0.1, tool-use in v0.2.

    Providers:

    - ``gemini``    — **Google-AI-Studio-Default.** Nutzt ``google-genai``-
      SDK, Key aus ``GEMINI_API_KEY`` oder ``GOOGLE_AIStudio_API_KEY``.
      Default-Model ``gemini-3.1-pro`` (Frontier).
    - ``anthropic`` — fuer User mit OpenClaw-Subscription. Shellt das
      lokale ``claude``-CLI (OAuth-Session des Max-Plan-Login), braucht
      keinen API-Key. Modelle: ``sonnet``, ``opus``, ``haiku``, oder
      voller Name (``claude-sonnet-4-6``).
    - ``anthropic`` — direkter API-Call via ``ANTHROPIC_API_KEY`` ENV.
      Fuer User ohne Subscription.
    - ``openai``    — ``OPENAI_API_KEY``.
    - ``ollama``    — lokal, kein Key. Default-Host ``http://localhost:11434``.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")
    type: Literal["agent"] = "agent"
    provider: Literal[
        "gemini", "anthropic", "openai", "ollama"
    ] = "gemini"
    model: str = Field(
        default="gemini-3.1-pro", min_length=1, max_length=128,
        description="Alias ('sonnet', 'opus') oder full name "
        "('gemini-3.1-pro', 'claude-sonnet-4-6', 'gpt-4o', 'llama3.1'). "
        "Default: gemini-3.1-pro (passend zum Default-Provider 'gemini').",
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
    """Blueprint eines wiederholbaren Jobs — persistiert in ``jobs``-Tabelle.

    Die ``spec`` ist der komplette Job-Payload (Typ + Args) und wird als
    JSON serialisiert. Der ``schedule`` ebenfalls.
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
    """Ein konkreter Lauf eines Jobs."""
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
    """Sub-Step eines Runs — fuer v0.2 Multi-Step-Agents."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: UUID
    seq: int
    kind: str                             # "tool_call" | "llm_message" | "log"
    label: str = ""
    started_at_ns: int = 0
    finished_at_ns: int = 0
    success: bool = False
    payload_json: str = "{}"
