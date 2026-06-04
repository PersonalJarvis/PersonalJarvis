"""Pydantic-Schema fuer Workflows — Defs, Steps, Trigger, Runs.

Sechs Step-Kinds — bewusst klein, damit Users klare Primitive komponieren
koennen ohne Turing-complete Node-Editor:

- ``brain_prompt``     → schickt ein Prompt an den aktiven BrainManager,
  schreibt die Antwort als ``output`` in den Run-Context.
- ``harness_dispatch`` → dispatcht einen HarnessTask (OpenClaw, Codex,
  Computer-Use, MCP-Remote, Python-Script).
- ``speak``            → emittiert ``AnnouncementRequested``-Event; die TTS-
  Pipeline hoert zu und spricht den Text. Ohne TTS: Event bleibt im Log.
- ``tool_call``        → fuehrt ein Tool aus der ``tool_registry`` aus.
- ``shell_cmd``        → fuehrt eine Shell-Command aus (``gws gmail +triage``,
  ``git status`` etc.). Captures stdout als Step-Output. Timeout + Output-Cap.
- ``telegram_send``    → schickt eine Nachricht via Telegram-Bot-API.
  Bot-Token kommt aus dem Credential Manager (Key ``telegram_bot_token``),
  Chat-ID aus Step oder ``[integrations.telegram]``-Config.

**Template-Variablen:** Jedes Step-Feld darf ``{{prev.output}}``,
``{{step_N.output}}`` oder ``{{input.<key>}}`` enthalten — der Runner
expandiert sie vor Execution. Kein Jinja (wuerde Risiko erhoehen),
nur simpler Placeholder-Replace.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------
# Trigger
# ---------------------------------------------------------------------

class ManualTrigger(BaseModel):
    """Workflow laeuft nur wenn User ``run`` klickt oder per API triggert."""
    model_config = ConfigDict(frozen=True, extra="forbid")
    type: Literal["manual"] = "manual"


class CronTrigger(BaseModel):
    """Cron-Expression (5 Felder, Standard-Syntax). Nutzt ``croniter``.

    Beispiele:
        ``30 7 * * *``   — taeglich 07:30
        ``0 */2 * * *``  — alle 2 Stunden
        ``0 9 * * 1-5``  — Werktags 09:00
    """
    model_config = ConfigDict(frozen=True, extra="forbid")
    type: Literal["cron"] = "cron"
    expression: str = Field(min_length=9, max_length=128,
                             description="Cron-Ausdruck mit 5 Feldern.")
    timezone: str = Field(default="local",
                          description="'local' oder IANA-Zone (z.B. 'Europe/Berlin').")


WorkflowTrigger = Annotated[
    ManualTrigger | CronTrigger,
    Field(discriminator="type"),
]


# ---------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------

class BrainPromptStep(BaseModel):
    """Schickt ein Prompt an den aktiven BrainManager.

    Der Runner schreibt die Antwort in ``run.outputs[step_id]``. Nachfolgende
    Steps koennen darauf via ``{{prev.output}}`` oder ``{{step_1.output}}``
    zugreifen.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["brain_prompt"] = "brain_prompt"
    label: str = Field(default="", max_length=128)
    prompt: str = Field(min_length=1, max_length=16_384)
    max_output_chars: int = Field(default=2000, ge=100, le=50_000)


class HarnessDispatchStep(BaseModel):
    """Dispatcht an einen Harness (openclaw, codex, computer-use, ...).

    Ergebnis ist ``stdout`` der finalen Runde; wird als ``output`` im
    Run-Context gespeichert.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["harness_dispatch"] = "harness_dispatch"
    label: str = Field(default="", max_length=128)
    harness: str = Field(min_length=1, max_length=64)
    prompt: str = Field(min_length=1, max_length=16_384)
    allow_computer_use: bool = False


class SpeakStep(BaseModel):
    """Emittiert einen ``AnnouncementRequested``-Event.

    Wenn die TTS-Pipeline laeuft, wird der Text gesprochen. Sonst
    bleibt das Event im Flight-Recorder und der Step gilt als erfolgreich.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["speak"] = "speak"
    label: str = Field(default="", max_length=128)
    text: str = Field(min_length=1, max_length=4096)
    priority: Literal["normal", "interrupt"] = "normal"
    language: str = Field(default="de", max_length=8)


class ToolCallStep(BaseModel):
    """Fuehrt einen Tool-Aufruf auf der Tool-Registry aus (Risk-Tier-konform)."""
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["tool_call"] = "tool_call"
    label: str = Field(default="", max_length=128)
    tool_name: str = Field(min_length=1, max_length=64)
    args: dict[str, Any] = Field(default_factory=dict)


class ShellCmdStep(BaseModel):
    """Fuehrt eine Shell-Command-Line aus. stdout wird als Step-Output
    persistiert, stderr in ``error`` wenn exit_code != 0.

    **Sicherheits-Hinweis:** Dieser Step-Typ ist maechtig — User, die fremde
    Workflows importieren, sollten ``command`` vor Aktivierung pruefen. Wir
    fuehren ``shell=False``, d.h. keine Pipe/Redirect-Unterstuetzung; fuer
    komplexere Pipelines mehrere shell_cmd-Steps hintereinander nutzen.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["shell_cmd"] = "shell_cmd"
    label: str = Field(default="", max_length=128)
    command: str = Field(min_length=1, max_length=4096,
                          description="Kommando-Zeile; whitespace-split zu argv.")
    cwd: str = Field(default="", max_length=512,
                      description="Working-Directory, leer = App-Verzeichnis.")
    timeout_s: float = Field(default=60.0, ge=1.0, le=900.0)
    max_output_chars: int = Field(default=8000, ge=200, le=200_000)


class TelegramSendStep(BaseModel):
    """Sendet eine Telegram-Nachricht via Bot-API.

    Bot-Token kommt aus dem Credential Manager; Chat-ID aus diesem Step
    (hat Vorrang) oder ``[integrations.telegram].chat_id`` in der Config.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["telegram_send"] = "telegram_send"
    label: str = Field(default="", max_length=128)
    text: str = Field(min_length=1, max_length=4096,
                       description="Nachrichten-Text; supports Markdown/HTML "
                       "je nach parse_mode der Config.")
    chat_id: str = Field(default="", max_length=64,
                          description="Leer = Default-Chat aus Config.")


WorkflowStep = Annotated[
    (BrainPromptStep | HarnessDispatchStep | SpeakStep | ToolCallStep
     | ShellCmdStep | TelegramSendStep),
    Field(discriminator="kind"),
]


# ---------------------------------------------------------------------
# WorkflowDef + Run
# ---------------------------------------------------------------------

class WorkflowDef(BaseModel):
    """Die Blaupause eines Workflows. Persistiert in Tabelle ``workflows``."""
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
    "pending",      # gerade angelegt, noch nicht gestartet
    "running",      # Steps werden abgearbeitet
    "completed",    # alle Steps erfolgreich
    "failed",       # ein Step hat ge-raised
    "cancelled",    # manuell abgebrochen
]


class WorkflowRun(BaseModel):
    """Ein konkreter Lauf eines WorkflowDef. Persistiert in ``workflow_runs``.

    Steps-Fortschritt steht in ``workflow_run_steps``; hier nur Aggregat.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    workflow_id: UUID
    state: WorkflowRunState = "pending"
    trigger: str = "manual"             # "manual" | "cron" | "event"
    started_at_ns: int = 0
    finished_at_ns: int = 0
    error: str | None = None
    input_json: str = "{}"              # User-Input beim Trigger (fuer {{input.X}}-Expansion)


# ---------------------------------------------------------------------
# Utility — Step-Preview-Label
# ---------------------------------------------------------------------

def step_display_label(step: WorkflowStep) -> str:
    """Gibt einen Display-Label fuer UI-Timelines zurueck — expliziter Label
    schlaegt generischer Kind-Beschreibung.
    """
    if step.label:
        return step.label
    if step.kind == "brain_prompt":
        return f"Brain: {step.prompt[:60]}..."
    if step.kind == "harness_dispatch":
        return f"{step.harness}: {step.prompt[:60]}..."
    if step.kind == "speak":
        return f"Sprich: {step.text[:60]}..."
    if step.kind == "tool_call":
        return f"Tool: {step.tool_name}"
    if step.kind == "shell_cmd":
        return f"Shell: {step.command[:60]}"
    if step.kind == "telegram_send":
        return f"Telegram: {step.text[:60]}"
    return step.kind
