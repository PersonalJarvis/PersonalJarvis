"""Pydantic schema for workflows — defs, steps, trigger, runs.

Six step kinds — deliberately small, so users can compose clear
primitives without a Turing-complete node editor:

- ``brain_prompt``     → sends a prompt to the active BrainManager,
  writes the reply as ``output`` into the run context.
- ``harness_dispatch`` → dispatches a HarnessTask (OpenClaw, Codex,
  Computer-Use, MCP-Remote, Python script).
- ``speak``            → emits an ``AnnouncementRequested`` event; the TTS
  pipeline listens and speaks the text. Without TTS: the event stays in the log.
- ``tool_call``        → executes a tool from the ``tool_registry``.
- ``shell_cmd``        → runs a shell command (``gws gmail +triage``,
  ``git status`` etc.). Captures stdout as the step output. Timeout + output cap.
- ``telegram_send``    → sends a message via the Telegram Bot API.
  The bot token comes from the credential manager (key ``telegram_bot_token``),
  the chat ID from the step or the ``[integrations.telegram]`` config.

**Template variables:** every step field may contain ``{{prev.output}}``,
``{{step_N.output}}``, or ``{{input.<key>}}`` — the runner expands them
before execution. No Jinja (would raise the risk), just a simple
placeholder replace.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------
# Trigger
# ---------------------------------------------------------------------

class ManualTrigger(BaseModel):
    """Workflow only runs when the user clicks ``run`` or triggers it via the API."""
    model_config = ConfigDict(frozen=True, extra="forbid")
    type: Literal["manual"] = "manual"


class CronTrigger(BaseModel):
    """Cron expression (5 fields, standard syntax). Uses ``croniter``.

    Examples:
        ``30 7 * * *``   — daily at 07:30
        ``0 */2 * * *``  — every 2 hours
        ``0 9 * * 1-5``  — weekdays at 09:00
    """
    model_config = ConfigDict(frozen=True, extra="forbid")
    type: Literal["cron"] = "cron"
    expression: str = Field(min_length=9, max_length=128,
                             description="Cron expression with 5 fields.")
    timezone: str = Field(default="local",
                          description="'local' or an IANA zone (e.g. 'Europe/Berlin').")


WorkflowTrigger = Annotated[
    ManualTrigger | CronTrigger,
    Field(discriminator="type"),
]


# ---------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------

class BrainPromptStep(BaseModel):
    """Sends a prompt to the active BrainManager.

    The runner writes the reply into ``run.outputs[step_id]``. Subsequent
    steps can access it via ``{{prev.output}}`` or ``{{step_1.output}}``.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["brain_prompt"] = "brain_prompt"
    label: str = Field(default="", max_length=128)
    prompt: str = Field(min_length=1, max_length=16_384)
    max_output_chars: int = Field(default=2000, ge=100, le=50_000)


class HarnessDispatchStep(BaseModel):
    """Dispatches to a harness (openclaw, codex, computer-use, ...).

    The result is the ``stdout`` of the final round; stored as ``output``
    in the run context.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["harness_dispatch"] = "harness_dispatch"
    label: str = Field(default="", max_length=128)
    harness: str = Field(min_length=1, max_length=64)
    prompt: str = Field(min_length=1, max_length=16_384)
    allow_computer_use: bool = False


class SpeakStep(BaseModel):
    """Emits an ``AnnouncementRequested`` event.

    If the TTS pipeline is running, the text is spoken. Otherwise
    the event stays in the flight recorder and the step counts as successful.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["speak"] = "speak"
    label: str = Field(default="", max_length=128)
    text: str = Field(min_length=1, max_length=4096)
    priority: Literal["normal", "interrupt"] = "normal"
    language: str = Field(default="de", max_length=8)


class ToolCallStep(BaseModel):
    """Executes a tool call against the tool registry (risk-tier compliant)."""
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["tool_call"] = "tool_call"
    label: str = Field(default="", max_length=128)
    tool_name: str = Field(min_length=1, max_length=64)
    args: dict[str, Any] = Field(default_factory=dict)


class ShellCmdStep(BaseModel):
    """Executes a shell command line. stdout is persisted as the step
    output, stderr in ``error`` when exit_code != 0.

    **Security note:** this step type is powerful — users who import
    third-party workflows should review ``command`` before activation. We
    run with ``shell=False``, i.e. no pipe/redirect support; for more
    complex pipelines, chain multiple shell_cmd steps.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["shell_cmd"] = "shell_cmd"
    label: str = Field(default="", max_length=128)
    command: str = Field(min_length=1, max_length=4096,
                          description="Command line; whitespace-split into argv.")
    cwd: str = Field(default="", max_length=512,
                      description="Working directory, empty = app directory.")
    timeout_s: float = Field(default=60.0, ge=1.0, le=900.0)
    max_output_chars: int = Field(default=8000, ge=200, le=200_000)


class TelegramSendStep(BaseModel):
    """Sends a Telegram message via the Bot API.

    The bot token comes from the credential manager; the chat ID from this
    step (takes precedence) or ``[integrations.telegram].chat_id`` in the config.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["telegram_send"] = "telegram_send"
    label: str = Field(default="", max_length=128)
    text: str = Field(min_length=1, max_length=4096,
                       description="Message text; supports Markdown/HTML "
                       "depending on the config's parse_mode.")
    chat_id: str = Field(default="", max_length=64,
                          description="Empty = default chat from config.")


WorkflowStep = Annotated[
    (BrainPromptStep | HarnessDispatchStep | SpeakStep | ToolCallStep
     | ShellCmdStep | TelegramSendStep),
    Field(discriminator="kind"),
]


# ---------------------------------------------------------------------
# WorkflowDef + Run
# ---------------------------------------------------------------------

class WorkflowDef(BaseModel):
    """The blueprint of a workflow. Persisted in the ``workflows`` table."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=1024)
    trigger: WorkflowTrigger
    steps: tuple[WorkflowStep, ...] = Field(default_factory=tuple, min_length=1)
    enabled: bool = True
    created_at_ns: int = 0
    created_by: str = "user"            # "user" | "seed" | "brain"
    tags: tuple[str, ...] = Field(default_factory=tuple)


WorkflowRunState = Literal[
    "pending",      # just created, not started yet
    "running",      # steps are being processed
    "completed",    # all steps succeeded
    "failed",       # a step raised
    "cancelled",    # manually cancelled
]


class WorkflowRun(BaseModel):
    """A concrete run of a WorkflowDef. Persisted in ``workflow_runs``.

    Step progress lives in ``workflow_run_steps``; this holds only the aggregate.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    workflow_id: UUID
    state: WorkflowRunState = "pending"
    trigger: str = "manual"             # "manual" | "cron" | "event"
    started_at_ns: int = 0
    finished_at_ns: int = 0
    error: str | None = None
    input_json: str = "{}"              # user input at trigger time (for {{input.X}} expansion)


# ---------------------------------------------------------------------
# Utility — step preview label
# ---------------------------------------------------------------------

def step_display_label(step: WorkflowStep) -> str:
    """Returns a display label for UI timelines — an explicit label
    beats a generic kind description.
    """
    if step.label:
        return step.label
    if step.kind == "brain_prompt":
        return f"Brain: {step.prompt[:60]}..."
    if step.kind == "harness_dispatch":
        return f"{step.harness}: {step.prompt[:60]}..."
    if step.kind == "speak":
        return f"Speak: {step.text[:60]}..."
    if step.kind == "tool_call":
        return f"Tool: {step.tool_name}"
    if step.kind == "shell_cmd":
        return f"Shell: {step.command[:60]}"
    if step.kind == "telegram_send":
        return f"Telegram: {step.text[:60]}"
    return step.kind
