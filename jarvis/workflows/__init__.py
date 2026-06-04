"""Workflow-System (Phase 6) — AI-Agent-Orchestration-Dashboard.

Ein **Workflow** ist eine benannte, wiederverwendbare Sequenz von Steps.
Getrennt von ``jarvis.tasks`` (single-action, persistente Task-Queue)
und ``jarvis.skills`` (voice/hotkey-triggered SKILL.md-Files):

- Tasks = "einmal ausfuehren, dann weg" (Erinnerungen, Ad-hoc).
- Skills = Natural-Language-Shortcuts (voice-triggered Macros).
- **Workflows = gehostete Multi-Step-Pipelines** mit Cron- oder Manual-
  Trigger, die Brain-Prompts, Harness-Calls, Tool-Aufrufe und TTS-Ansagen
  zu einer Sequenz verketten. Das ist die AI-Agent-Antwort auf n8n.

Gehoert in Layer L6 (Orchestrator) laut Plan §3.
"""
from __future__ import annotations

from .runner import WorkflowRunner
from .scheduler import WorkflowScheduler
from .schema import (
    BrainPromptStep,
    HarnessDispatchStep,
    ShellCmdStep,
    SpeakStep,
    TelegramSendStep,
    ToolCallStep,
    WorkflowDef,
    WorkflowRun,
    WorkflowRunState,
    WorkflowStep,
    WorkflowTrigger,
)
from .seed import SEED_WORKFLOWS, ensure_seed_workflows
from .store import WorkflowStore

__all__ = [
    "BrainPromptStep",
    "HarnessDispatchStep",
    "ShellCmdStep",
    "SpeakStep",
    "TelegramSendStep",
    "ToolCallStep",
    "WorkflowDef",
    "WorkflowRun",
    "WorkflowRunState",
    "WorkflowRunner",
    "WorkflowScheduler",
    "WorkflowStep",
    "WorkflowStore",
    "WorkflowTrigger",
    "SEED_WORKFLOWS",
    "ensure_seed_workflows",
]
